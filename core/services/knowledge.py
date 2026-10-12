from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import re
from functools import lru_cache
from pathlib import Path
from sqlalchemy.orm import Session

from core.config import get_settings
from core.db.models import KnowledgeBase, KnowledgeChunk, KnowledgeDocument, KnowledgeParentChunk
from core.integrations.llm import OpenAICompatibleProvider
from core.integrations import vector_store as vector_store_module
from core.services.rag import retrieve
from core.services.uploads import (
    DOC_TYPES,
    LANGCHAIN_EXTRA_SUFFIXES,
    LANGCHAIN_EXTRA_TYPES,
    extract_document_text,
    sanitize_extracted_text,
)


logger = logging.getLogger(__name__)

SUPPORTED_KNOWLEDGE_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".pdf", ".docx"}
SUPPORTED_TEXT_TYPES = {"text/plain", "text/markdown", "application/markdown", "text/csv"}
SUPPORTED_FILE_TYPES = DOC_TYPES


class KnowledgeDocumentError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400, record_failed: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.record_failed = record_failed


def mark_document_reindexing(db: Session, *, document_id: int) -> bool:
    """
    原子守卫：仅当文档不处于 indexing 时置为 indexing。返回是否成功抢到。

    🛡️ 一条带 WHERE status != 'indexing' 的原子 UPDATE + 检查 rowcount，
        防止并发重复执行（rabbitmq → BackgroundTasks，无需 Redis 锁）。
    """
    from sqlalchemy import update
    result = db.execute(
        update(KnowledgeDocument)
        .where(KnowledgeDocument.id == document_id, KnowledgeDocument.status != "indexing")
        .values(status="indexing", error_message="", chunk_count=0)
    )
    db.commit()
    return result.rowcount > 0


def create_knowledge_base(db: Session, *, workspace_id: int, user_id: int, name: str, description: str = "") -> KnowledgeBase:
    kb = KnowledgeBase(workspace_id=workspace_id, name=name, description=description, created_by=user_id)
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return kb


def knowledge_base_summary(kb: KnowledgeBase, document_count: int = 0) -> dict:
    return {
        "id": kb.id,
        "name": kb.name,
        "description": kb.description,
        "document_count": document_count,
        "enabled": bool(getattr(kb, "enabled", True)),
        # updated_at 为 NULL 时回退 created_at（兼容未跑回填的老库）。
        "updated_at": (
            (getattr(kb, "updated_at", None) or kb.created_at).isoformat()
            if getattr(kb, "updated_at", None) or kb.created_at
            else None
        ),
        "created_at": kb.created_at.isoformat() if kb.created_at else None,
    }


def document_payload(document: KnowledgeDocument, chunk_count: int = 0) -> dict:
    stored_chunk_count = getattr(document, "chunk_count", 0) or 0
    effective_chunk_count = stored_chunk_count if stored_chunk_count or document.status == "failed" else chunk_count
    text_preview = (getattr(document, "text_preview", "") or document.text or "")[:240]
    return {
        "id": document.id,
        "knowledge_base_id": document.knowledge_base_id,
        "filename": document.filename,
        "title": getattr(document, "title", "") or document.filename,
        "content_type": document.content_type,
        "source_type": getattr(document, "source_type", "text") or "text",
        "status": document.status,
        "chunk_count": effective_chunk_count,
        "text_preview": text_preview,
        "error_message": getattr(document, "error_message", "") or None,
        "segment_config": getattr(document, "segment_config", None),
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "updated_at": document.updated_at.isoformat() if getattr(document, "updated_at", None) else None,
    }


def add_document(
    db: Session,
    *,
    workspace_id: int,
    kb: KnowledgeBase,
    filename: str | None = None,
    text: str | None = None,
    content: str | None = None,
    content_type: str = "text/plain",
    source_type: str = "text",
    content_base64: str | None = None,
    title: str | None = None,
    runtime_config: dict | None = None,
    segment_config: dict | None = None,
    defer_indexing: bool = False,
) -> KnowledgeDocument:
    source_type = source_type or "text"
    filename = _safe_filename(filename or title or "document.txt")
    title = (title or filename).strip()[:255]
    try:
        prepared_text, normalized_filename, normalized_content_type = _prepare_document_payload(
            filename=filename,
            text=text,
            content=content,
            content_type=content_type,
            source_type=source_type,
            content_base64=content_base64,
        )
    except KnowledgeDocumentError as exc:
        if exc.record_failed:
            return _create_failed_document(
                db,
                kb=kb,
                filename=filename,
                title=title,
                content_type=content_type,
                source_type=source_type,
                error_message=str(exc),
            )
        raise

    document = KnowledgeDocument(
        knowledge_base_id=kb.id,
        filename=normalized_filename,
        title=title or normalized_filename,
        content_type=normalized_content_type,
        source_type=source_type,
        text=prepared_text,
        text_preview=_preview(prepared_text),
        chunk_count=0,
        error_message="",
        # 异步入库：文本已就绪、重活待后台，先标 indexing（前端显示「索引中」）；同步路径仍标 uploaded。
        status="indexing" if defer_indexing else "uploaded",
        segment_config=segment_config or None,
    )
    db.add(document)
    db.flush()
    if defer_indexing:
        # 仅持久化提取后的文本，分块/向量化/落库交由后台 run_document_ingestion 执行。
        db.commit()
        db.refresh(document)
        return document
    index_document(
        db,
        workspace_id=workspace_id,
        kb=kb,
        document=document,
        runtime_config=runtime_config,
        clear_existing=False,
    )
    db.commit()
    db.refresh(document)
    return document


def run_document_ingestion(*, document_id: int, workspace_id: int, kb_id: int) -> None:
    """
    后台执行单文档入库（分块 + 向量化 + 落库）。

    🎯 由上传接口经 FastAPI BackgroundTasks 调度，把耗时的 embedding/写库移出 HTTP 请求。
    🛡️ 自开独立 Session（请求 Session 在响应后已关闭）；任何异常自行落库为 failed + 清理半成品，
        绝不抛出（后台任务无处可抛）。状态机：indexing → indexed / failed。
    """
    from core.db.session import SessionLocal

    db = SessionLocal()
    try:
        kb = db.get(KnowledgeBase, kb_id)
        document = db.get(KnowledgeDocument, document_id)
        if not kb or not document:
            return
        try:
            index_document(db, workspace_id=workspace_id, kb=kb, document=document, clear_existing=True)
            db.commit()
        except Exception as exc:
            db.rollback()
            try:
                vector_store_module.vector_store.delete(
                    filters={"workspace_id": workspace_id, "knowledge_base_id": kb_id, "document_id": document_id}
                )
            except Exception:
                pass
            document = db.get(KnowledgeDocument, document_id)
            if document:
                db.query(KnowledgeChunk).filter(KnowledgeChunk.document_id == document_id).delete(synchronize_session=False)
                db.query(KnowledgeParentChunk).filter(KnowledgeParentChunk.document_id == document_id).delete(synchronize_session=False)
                document.status = "failed"
                document.error_message = _sanitize_error(str(exc))
                document.chunk_count = 0
                db.commit()
            logger.exception("Background document ingestion failed: document_id=%s", document_id)
    finally:
        db.close()


def run_kb_reindex(*, workspace_id: int, kb_id: int, job_id: str) -> None:
    """后台重建整个知识库索引，并把进度/结果回写 Redis job。自开 Session、吞异常。"""
    from core.db.session import SessionLocal
    from core.services.rag_cache import redis_store

    db = SessionLocal()
    try:
        kb = db.get(KnowledgeBase, kb_id)
        if not kb:
            return
        try:
            summary = reindex_knowledge_base(db, workspace_id=workspace_id, kb=kb)
            status = "failed" if summary["documents_failed"] and not summary["documents_indexed"] else "succeeded"
            message = (
                f"Rebuilt {summary['chunks_indexed']} chunks for {summary['documents_indexed']} documents."
                if status == "succeeded" else "Knowledge base reindex failed for all documents."
            )
            redis_store.set_job(job_id, {"job_id": job_id, "knowledge_base_id": kb_id, "status": status, "message": message, **summary})
        except Exception as exc:
            db.rollback()
            redis_store.set_job(job_id, {"job_id": job_id, "knowledge_base_id": kb_id, "status": "failed", "message": _sanitize_error(str(exc))})
            logger.exception("Background knowledge base reindex failed: kb_id=%s", kb_id)
    finally:
        db.close()


def recover_interrupted_ingestion(db: Session) -> int:
    """
    启动时崩溃恢复：把卡在 indexing 的文档复位为 failed。

    🎯 BackgroundTasks 在进程内运行，进程重启会丢失在途任务，留下永远卡在 indexing 的文档。
        启动时（无任务在跑）统一复位，允许用户手动重新索引。
    """
    stuck = db.query(KnowledgeDocument).filter(KnowledgeDocument.status == "indexing").all()
    for document in stuck:
        document.status = "failed"
        document.error_message = "入库被中断（服务重启），请重新索引该文档。"
        document.chunk_count = 0
    if stuck:
        db.commit()
    return len(stuck)


def reindex_knowledge_base(db: Session, *, workspace_id: int, kb: KnowledgeBase) -> dict:
    documents = (
        db.query(KnowledgeDocument)
        .filter(KnowledgeDocument.knowledge_base_id == kb.id)
        .order_by(KnowledgeDocument.id.asc())
        .all()
    )
    indexed = 0
    failed = 0
    chunk_count = 0
    errors = []
    for document in documents:
        document_id = document.id
        filename = document.filename
        if not (document.text or "").strip():
            document.status = "failed"
            document.error_message = "Document has no extracted text to index"
            document.chunk_count = 0
            failed += 1
            errors.append({"document_id": document_id, "filename": filename, "error": document.error_message})
            db.commit()
            continue
        try:
            indexed_chunks = index_document(db, workspace_id=workspace_id, kb=kb, document=document, clear_existing=True)
            db.commit()
            chunk_count += indexed_chunks
            indexed += 1
        except Exception as exc:
            db.rollback()
            try:
                vector_store_module.vector_store.delete(
                    filters={"workspace_id": workspace_id, "knowledge_base_id": kb.id, "document_id": document_id}
                )
            except Exception:
                pass
            failed += 1
            document = db.get(KnowledgeDocument, document_id)
            if not document:
                errors.append({"document_id": document_id, "filename": filename, "error": _sanitize_error(str(exc))})
                continue
            db.query(KnowledgeChunk).filter(KnowledgeChunk.document_id == document_id).delete(synchronize_session=False)
            document.status = "failed"
            document.error_message = _sanitize_error(str(exc))
            document.chunk_count = 0
            db.commit()
            errors.append({"document_id": document_id, "filename": filename, "error": document.error_message})
    return {
        "documents_total": len(documents),
        "documents_indexed": indexed,
        "documents_failed": failed,
        "chunks_indexed": chunk_count,
        "errors": errors[:10],
    }


def index_document(
    db: Session,
    *,
    workspace_id: int,
    kb: KnowledgeBase,
    document: KnowledgeDocument,
    runtime_config: dict | None = None,
    clear_existing: bool = True,
) -> int:
    if clear_existing:
        vector_store_module.vector_store.delete(
            filters={"workspace_id": workspace_id, "knowledge_base_id": kb.id, "document_id": document.id}
        )
        db.query(KnowledgeChunk).filter(KnowledgeChunk.document_id == document.id).delete(synchronize_session=False)
        db.query(KnowledgeParentChunk).filter(KnowledgeParentChunk.document_id == document.id).delete(synchronize_session=False)

    document.status = "indexing"
    document.error_message = ""
    document.chunk_count = 0

    # 惰性导入避免与 ingestion 包的循环依赖（knowledge 在模块层不依赖 ingestion）
    from core.services.ingestion.context import IngestionContext
    from core.services.ingestion.nodes import ChunkNode, EmbedNode, StoreNode
    from core.services.ingestion.pipeline import IngestionPipeline, IngestionPipelineError

    settings = get_settings()
    ctx = IngestionContext(
        workspace_id=workspace_id,
        knowledge_base_id=kb.id,
        document_id=document.id,
        filename=document.filename,
        content_type=document.content_type,
        text=document.text,
        title=document.title or document.filename or "",
        segment_config=document.segment_config or {},
        runtime_config=runtime_config,
    )
    pipeline = IngestionPipeline([
        ChunkNode(),
        EmbedNode(OpenAICompatibleProvider()),
        StoreNode(
            db,
            vector_store_module.vector_store,
            title=document.title or document.filename,
            filename=document.filename,
            embedding_model=settings.openai_embedding_model,
        ),
    ])
    try:
        pipeline.run(ctx)
    except IngestionPipelineError as exc:
        document.ingestion_log = ctx.logs
        document.status = "failed"
        document.error_message = _sanitize_error(str(exc))
        document.chunk_count = 0
        raise
    document.ingestion_log = ctx.logs
    document.chunk_count = len(ctx.children)
    document.status = "indexed"
    return len(ctx.children)


def delete_document(db: Session, *, workspace_id: int, document: KnowledgeDocument) -> None:
    chunks = db.query(KnowledgeChunk).filter(KnowledgeChunk.document_id == document.id).all()
    vector_store_module.vector_store.delete(filters={"workspace_id": workspace_id, "knowledge_base_id": document.knowledge_base_id, "document_id": document.id})
    for chunk in chunks:
        db.delete(chunk)
    db.query(KnowledgeParentChunk).filter(KnowledgeParentChunk.document_id == document.id).delete(synchronize_session=False)
    db.delete(document)
    db.commit()


def delete_knowledge_base(db: Session, *, workspace_id: int, kb: KnowledgeBase) -> None:
    documents = db.query(KnowledgeDocument).filter(KnowledgeDocument.knowledge_base_id == kb.id).all()
    for document in documents:
        vector_store_module.vector_store.delete(filters={"workspace_id": workspace_id, "knowledge_base_id": kb.id, "document_id": document.id})
        db.query(KnowledgeChunk).filter(KnowledgeChunk.document_id == document.id).delete(synchronize_session=False)
    db.query(KnowledgeParentChunk).filter(KnowledgeParentChunk.knowledge_base_id == kb.id).delete(synchronize_session=False)
    db.query(KnowledgeDocument).filter(KnowledgeDocument.knowledge_base_id == kb.id).delete(synchronize_session=False)
    db.delete(kb)
    db.commit()


def list_document_chunks(db: Session, *, document_id: int) -> list[dict]:
    chunks = (
        db.query(KnowledgeChunk)
        .filter(KnowledgeChunk.document_id == document_id)
        .order_by(KnowledgeChunk.chunk_index.asc())
        .all()
    )
    return [
        {
            "id": chunk.id,
            "chunk_index": chunk.chunk_index,
            "chunk_id": chunk.chunk_id or chunk.vector_id,
            "parent_id": chunk.parent_id or chunk.vector_id,
            "text": chunk.text,
            "vector_id": chunk.vector_id,
            "embedding_model": chunk.embedding_model or "",
            "embedding_dimension": chunk.embedding_dimension or 0,
            "content_hash": chunk.content_hash or "",
            "title": chunk.title or "",
            "page": chunk.page,
            "section": chunk.section or "",
        }
        for chunk in chunks
    ]


def search_knowledge(
    db: Session,
    *,
    workspace_id: int,
    knowledge_base_ids: list[int],
    query: str,
    top_k: int = 4,
    runtime_config: dict | None = None,
) -> list[dict]:
    result = retrieve(
        db,
        workspace_id=workspace_id,
        knowledge_base_ids=knowledge_base_ids,
        query=query,
        config={"top_k": top_k},
        runtime_config=runtime_config,
    )
    return result.sources


def split_text(text: str, *, chunk_size: int = 700) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    return [cleaned[index : index + chunk_size] for index in range(0, len(cleaned), chunk_size)]


# ── 切分基础设施：token 计长 + 结构保留归一化 + CN/EN 递归分隔符 ──────────────

# 递归分隔符优先级：段落 > 行 > 中文句末 > 英文句末 > 中文子句 > 英文子句 > 空格 > 字符。
# 让切点尽量落在自然语义边界，而非定长字符窗里把句子/词从中间截断。
_RECURSIVE_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", ". ", "! ", "? ", "; ", "，", ", ", " ", ""]


@lru_cache(maxsize=1)
def _token_encoder():
    """cl100k_base 编码器（懒加载+缓存）；离线不可用时返回 None，调用方回退到字符长度。"""
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def _token_len(text: str) -> int:
    """token 计长（embedding/LLM 的真实长度单位）；编码器不可用时退化为字符数。"""
    encoder = _token_encoder()
    if encoder is None:
        return len(text)
    try:
        return len(encoder.encode(text))
    except Exception:
        return len(text)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_keep_structure(text: str) -> str:
    r"""轻量归一化：统一换行、去行尾空白、压缩 3+ 连续空行为 1 个空行。

    关键区别于旧实现的 `re.sub(r"\s+", " ")`——保留段落/换行结构，递归切分才能命中边界。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _build_splitter(*, chunk_size: int, chunk_overlap: int, length_unit: str):
    """构造边界感知递归切分器；length_unit='token' 用 tiktoken 计长，否则按字符。"""
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    length_function = _token_len if length_unit == "token" else len
    chunk_size = max(int(chunk_size), 1)
    chunk_overlap = max(min(int(chunk_overlap), chunk_size - 1), 0)
    return RecursiveCharacterTextSplitter(
        separators=_RECURSIVE_SEPARATORS,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=length_function,
        keep_separator=True,
    )


def _contextual_prefix(title: str | None, section: str | None) -> str:
    """拼「文档标题 · 章节面包屑」作为 child 向量化前缀（上下文增强）。"""
    if not get_settings().rag_chunk_contextual_embed:
        return ""
    parts = [p.strip() for p in (title, section) if p and p.strip()]
    return " · ".join(parts)


def _embed_text(prefix: str, text: str) -> str:
    """组合上下文前缀与正文：仅用于送入 embedding，落库/展示的 text 保持干净。"""
    return f"{prefix}\n{text}" if prefix else text


def chunk_csv(
    text: str,
    *,
    kb_id: int,
    document_id: int,
    rows_per_child: int | None = None,
    rows_per_parent: int | None = None,
) -> tuple[list[dict], list[dict]]:
    """CSV 结构化分段：每个 child = 表头 + 若干数据行（自带表头，语义自洽）。返回 (children, parents)。

    rows_per_child/parent 为 None 时按 token 预算动态定行（宽表少装、窄表多装），避免固定 5 行
    在宽表里超长、在窄表里过碎；显式传入则尊重调用方（向后兼容）。
    """
    import csv
    import io

    settings = get_settings()
    children: list[dict] = []
    parents: list[dict] = []
    rows = [row for row in csv.reader(io.StringIO(text)) if any((cell or "").strip() for cell in row)]
    if not rows:
        return children, parents
    header_line = ", ".join((cell or "").strip() for cell in rows[0])
    data_rows = rows[1:] or [rows[0]]  # 仅表头时把表头当作唯一数据行

    def _row_text(row: list[str]) -> str:
        return ", ".join((cell or "").strip() for cell in row)

    def _rows_text(group: list[list[str]]) -> str:
        body = "\n".join(_row_text(row) for row in group)
        return f"{header_line}\n{body}"

    # 动态定行：表头 + 平均单行 token 成本，决定一个预算能容纳几行（至少 1 行）。
    if rows_per_child is None or rows_per_parent is None:
        header_tokens = _token_len(header_line)
        sample = data_rows[: min(len(data_rows), 20)]
        avg_row_tokens = max(1, sum(_token_len(_row_text(r)) for r in sample) // max(len(sample), 1))
        dyn_child = max(1, (settings.rag_chunk_csv_child_tokens - header_tokens) // avg_row_tokens)
        dyn_parent = max(dyn_child, (settings.rag_chunk_csv_parent_tokens - header_tokens) // avg_row_tokens)
        rows_per_child = rows_per_child or dyn_child
        rows_per_parent = rows_per_parent or dyn_parent

    rows_per_child = max(1, int(rows_per_child))
    rows_per_parent = max(rows_per_child, int(rows_per_parent))

    parent_index = 0
    for p_start in range(0, len(data_rows), rows_per_parent):
        p_rows = data_rows[p_start : p_start + rows_per_parent]
        parent_id = f"kb{kb_id}-doc{document_id}-csv-parent{parent_index}"
        parent_text = _rows_text(p_rows)
        parents.append({
            "parent_id": parent_id,
            "text": parent_text,
            "content_hash": _hash(parent_text),
        })
        child_index = 0
        for c_start in range(0, len(p_rows), rows_per_child):
            child_text = _rows_text(p_rows[c_start : c_start + rows_per_child])
            children.append({
                "parent_id": parent_id,
                "chunk_id": f"{parent_id}-child{child_index}",
                "text": child_text,
                "page": None,
                "section": "",
                "content_hash": _hash(child_text),
            })
            child_index += 1
        parent_index += 1
    return children, parents


def _split_parent_child(
    text: str,
    *,
    kb_id: int,
    document_id: int,
    parent_size: int,
    child_size: int,
    parent_overlap: int,
    child_overlap: int,
    length_unit: str = "token",
    title: str | None = None,
    section: str | None = None,
) -> tuple[list[dict], list[dict]]:
    r"""边界感知 parent-child 切分核心，返回 (children, parents)。

    - 用递归分隔符在自然语义边界切分（不再 `\s+→空格` 压平结构）。
    - parent 与 child 各自带 overlap，跨块语义不被切断（旧实现 parent 无重叠）。
    - child 额外携带 embed_text（上下文增强前缀），仅用于向量化；落库 text 保持干净。
    """
    cleaned = _normalize_keep_structure(text)
    children: list[dict] = []
    parents: list[dict] = []
    if not cleaned:
        return children, parents

    parent_splitter = _build_splitter(chunk_size=parent_size, chunk_overlap=parent_overlap, length_unit=length_unit)
    child_splitter = _build_splitter(chunk_size=child_size, chunk_overlap=child_overlap, length_unit=length_unit)
    prefix = _contextual_prefix(title, section)

    parent_index = 0
    for parent_text in parent_splitter.split_text(cleaned):
        parent_text = parent_text.strip()
        if not parent_text:
            continue
        parent_id = f"kb{kb_id}-doc{document_id}-parent{parent_index}"
        parents.append({
            "parent_id": parent_id,
            "text": parent_text,
            "content_hash": _hash(parent_text),
        })
        child_index = 0
        for child_text in child_splitter.split_text(parent_text):
            child_text = child_text.strip()
            if not child_text:
                continue
            children.append({
                "parent_id": parent_id,
                "chunk_id": f"{parent_id}-child{child_index}",
                "text": child_text,
                "embed_text": _embed_text(prefix, child_text),
                "page": None,
                "section": section or "",
                "content_hash": _hash(child_text),
            })
            child_index += 1
        parent_index += 1
    return children, parents


def chunk_document(
    text: str,
    *,
    content_type: str,
    kb_id: int,
    document_id: int,
    segment_config: dict | None = None,
    title: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """入库分段总分发器，返回 (children, parents)。

    - CSV(auto 模式) 走结构化分段（每行自带表头）。
    - hierarchy 模式按 Markdown 标题切，超长标题块二次切分，并生成对应父块（small-to-big 生效）。
    - custom 模式按用户「字数上限/重合度」（字符单位）切。
    - 默认/auto 按 token 预算做边界感知 parent-child 切分。
    """
    cfg = segment_config or {}
    seg_mode = cfg.get("segment_mode", "auto")
    settings = get_settings()
    if "csv" in (content_type or "").lower() and seg_mode == "auto":
        return chunk_csv(text, kb_id=kb_id, document_id=document_id)
    if seg_mode == "hierarchy":
        return _chunk_by_hierarchy(
            text, kb_id=kb_id, document_id=document_id,
            max_level=cfg.get("hierarchy_level", 3),
            keep_hierarchy_info=cfg.get("keep_hierarchy_info", True),
            title=title,
        )
    if seg_mode == "custom":
        max_len = max(int(cfg.get("max_chunk_len", 1600)), 1)
        overlap_pct = max(0, min(int(cfg.get("overlap_pct", 10)), 50))
        child_len = max(int(max_len * 0.35), 1)
        return _split_parent_child(
            text, kb_id=kb_id, document_id=document_id,
            parent_size=max_len,
            child_size=child_len,
            parent_overlap=int(max_len * overlap_pct / 100),
            child_overlap=int(child_len * overlap_pct / 100),
            length_unit="char",
            title=title,
        )
    return _split_parent_child(
        text, kb_id=kb_id, document_id=document_id,
        parent_size=settings.rag_chunk_parent_tokens,
        child_size=settings.rag_chunk_child_tokens,
        parent_overlap=settings.rag_chunk_parent_overlap_tokens,
        child_overlap=settings.rag_chunk_child_overlap_tokens,
        length_unit="token",
        title=title,
    )


def split_parent_child(
    text: str,
    *,
    kb_id: int,
    document_id: int,
    parent_size: int = 1600,
    child_size: int = 520,
    overlap: int = 80,
) -> list[dict]:
    """向后兼容包装：字符单位边界感知 parent-child 切分，仅返回 children。"""
    children, _ = _split_parent_child(
        text, kb_id=kb_id, document_id=document_id,
        parent_size=parent_size, child_size=child_size,
        parent_overlap=overlap, child_overlap=overlap,
        length_unit="char",
    )
    return children


def _emit_hierarchy_node(
    children: list[dict],
    parents: list[dict],
    *,
    kb_id: int,
    document_id: int,
    node_index: int,
    heading: str | None,
    body: str,
    section: str,
    title: str | None,
) -> None:
    """把一个标题节点（标题+正文）落成 1 个唯一父块 + 1..N 个 child。

    正文不超 child 预算时保持单块（含标题，语义自洽，兼容历史行为）；
    超长时二次切分，每块前置标题保留小标题语境，避免「整章一个超大块」稀释向量。
    """
    settings = get_settings()
    full_text = f"{heading}\n{body}" if heading else body
    parent_id = f"kb{kb_id}-doc{document_id}-hnode{node_index}"
    parents.append({
        "parent_id": parent_id,
        "text": full_text,
        "content_hash": _hash(full_text),
    })
    if _token_len(full_text) <= settings.rag_chunk_child_tokens:
        pieces = [full_text]
    else:
        splitter = _build_splitter(
            chunk_size=settings.rag_chunk_child_tokens,
            chunk_overlap=settings.rag_chunk_child_overlap_tokens,
            length_unit="token",
        )
        pieces = [
            (f"{heading}\n{piece.strip()}" if heading else piece.strip())
            for piece in splitter.split_text(body)
            if piece.strip()
        ] or [full_text]
    prefix = _contextual_prefix(title, section)
    for child_index, piece in enumerate(pieces):
        children.append({
            "parent_id": parent_id,
            "chunk_id": f"{parent_id}-child{child_index}",
            "text": piece,
            "embed_text": _embed_text(prefix, piece),
            "page": None,
            "section": section,
            "content_hash": _hash(piece),
        })


def _chunk_by_hierarchy(
    text: str,
    *,
    kb_id: int,
    document_id: int,
    max_level: int = 3,
    keep_hierarchy_info: bool = True,
    title: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """按 Markdown 标题层级切分，返回 (children, parents)。

    每个标题节点对应一个唯一父块（修复旧实现「同级标题共享 parent_id」的 bug，让 small-to-big
    父块扩展真正生效）；超 child 预算的正文二次切分；无标题时退化为默认 token parent-child（仍带父块）。
    """
    cleaned = text.strip()
    children: list[dict] = []
    parents: list[dict] = []
    if not cleaned:
        return children, parents

    heading_pattern = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)
    all_matches = list(heading_pattern.finditer(cleaned))
    matches = [m for m in all_matches if len(m.group(1)) <= max_level]
    if not matches:
        # 退化：无（符合层级的）标题则走默认 token parent-child 切分
        settings = get_settings()
        return _split_parent_child(
            cleaned, kb_id=kb_id, document_id=document_id,
            parent_size=settings.rag_chunk_parent_tokens,
            child_size=settings.rag_chunk_child_tokens,
            parent_overlap=settings.rag_chunk_parent_overlap_tokens,
            child_overlap=settings.rag_chunk_child_overlap_tokens,
            length_unit="token",
            title=title,
        )

    node_index = 0
    # 第一个标题之前的「前言/介绍」文本，防止丢失
    intro_text = cleaned[: matches[0].start()].strip()
    if intro_text:
        _emit_hierarchy_node(
            children, parents, kb_id=kb_id, document_id=document_id, node_index=node_index,
            heading=None, body=intro_text,
            section="前言" if keep_hierarchy_info else "", title=title,
        )
        node_index += 1

    # 层级路径栈，记录当前的 (level, heading_text) 元组，用于生成章节面包屑
    path_stack: list[tuple[int, str]] = []
    for i, match in enumerate(matches):
        level = len(match.group(1))
        heading_text = match.group(2).strip()
        start_pos = match.end()
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(cleaned)
        body = cleaned[start_pos:end_pos].strip()

        # 退栈：栈顶层级 >= 当前层级则弹出（同级兄弟互相替换，而非错误嵌套）
        while path_stack and path_stack[-1][0] >= level:
            path_stack.pop()
        path_stack.append((level, f"H{level}: {heading_text}"))
        section_path = " > ".join(item[1] for item in path_stack) if keep_hierarchy_info else ""

        if not body:
            continue
        _emit_hierarchy_node(
            children, parents, kb_id=kb_id, document_id=document_id, node_index=node_index,
            heading=heading_text, body=body, section=section_path, title=title,
        )
        node_index += 1
    return children, parents


def split_by_hierarchy(
    text: str,
    *,
    kb_id: int,
    document_id: int,
    max_level: int = 3,
    keep_hierarchy_info: bool = True,
) -> list[dict]:
    """向后兼容包装：仅返回 hierarchy children（父块由 chunk_document 路径另取）。"""
    children, _ = _chunk_by_hierarchy(
        text, kb_id=kb_id, document_id=document_id,
        max_level=max_level, keep_hierarchy_info=keep_hierarchy_info,
    )
    return children


def extract_upload_text(*, filename: str, content_type: str, content_base64: str) -> str:
    """从上传的原始文件（base64）提取纯文本，不落库、不嵌入。供「上传前预览切片」复用。

    复用 `_prepare_document_payload` 的全部校验（类型/大小/解析）。失败抛 KnowledgeDocumentError。
    """
    text, _, _ = _prepare_document_payload(
        filename=filename,
        text=None,
        content=None,
        content_type=content_type,
        source_type="file",
        content_base64=content_base64,
    )
    return text


def _prepare_document_payload(
    *,
    filename: str,
    text: str | None,
    content: str | None,
    content_type: str,
    source_type: str,
    content_base64: str | None,
) -> tuple[str, str, str]:
    normalized_filename = _safe_filename(filename)
    normalized_content_type = (content_type or "text/plain").strip().lower()
    if source_type == "text":
        payload_text = content if content is not None else text
        if payload_text is None or not payload_text.strip():
            raise KnowledgeDocumentError("Invalid document payload")
        payload_text = sanitize_extracted_text(payload_text)
        if not payload_text.strip():
            raise KnowledgeDocumentError("Invalid document payload")
        if normalized_content_type not in SUPPORTED_TEXT_TYPES:
            normalized_content_type = "text/plain"
        return payload_text, normalized_filename, normalized_content_type

    if source_type != "file":
        raise KnowledgeDocumentError("Invalid document payload")
    if not content_base64:
        raise KnowledgeDocumentError("Invalid document payload")
    if not _is_supported_file(normalized_filename, normalized_content_type):
        raise KnowledgeDocumentError("Unsupported file type")
    raw = _decode_base64(content_base64)
    if len(raw) > get_settings().upload_max_bytes:
        raise KnowledgeDocumentError("Document too large", status_code=413)
    try:
        extracted = sanitize_extracted_text(extract_document_text(normalized_filename, normalized_content_type, raw))
    except ValueError as exc:
        raise KnowledgeDocumentError("Document text extraction failed", status_code=422, record_failed=True) from exc
    if not extracted.strip():
        raise KnowledgeDocumentError(
            "未能从该文件提取到文本，可能是扫描件 / 图片型 PDF。请上传文本版文件，或先自行 OCR 转文字。",
            status_code=422, record_failed=True,
        )
    return extracted, normalized_filename, normalized_content_type


def _create_failed_document(
    db: Session,
    *,
    kb: KnowledgeBase,
    filename: str,
    title: str,
    content_type: str,
    source_type: str,
    error_message: str,
) -> KnowledgeDocument:
    document = KnowledgeDocument(
        knowledge_base_id=kb.id,
        filename=_safe_filename(filename),
        title=(title or filename)[:255],
        content_type=(content_type or "application/octet-stream")[:120],
        source_type=source_type,
        text="",
        text_preview="",
        chunk_count=0,
        error_message=_sanitize_error(error_message),
        status="failed",
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def _safe_filename(filename: str) -> str:
    cleaned = Path(filename).name.strip()
    return cleaned[:255] or "document.txt"


def _preview(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:240]


def _is_supported_file(filename: str, content_type: str) -> bool:
    suffix = Path(filename).suffix.lower()
    if content_type in SUPPORTED_FILE_TYPES or suffix in SUPPORTED_KNOWLEDGE_SUFFIXES:
        return True
    return get_settings().ingest_langchain_loaders and (
        content_type in LANGCHAIN_EXTRA_TYPES or suffix in LANGCHAIN_EXTRA_SUFFIXES
    )


def _decode_base64(content_base64: str) -> bytes:
    payload = content_base64.split(",", 1)[1] if content_base64.startswith("data:") and "," in content_base64 else content_base64
    try:
        return base64.b64decode(payload, validate=True)
    except binascii.Error as exc:
        raise KnowledgeDocumentError("Invalid document payload") from exc


def _sanitize_error(message: str) -> str:
    cleaned = re.sub(r"[A-Za-z]:\\[^\s]+", "[path]", str(message))
    cleaned = re.sub(r"(?i)(sk-[A-Za-z0-9_-]+|api[_-]?key\s*[:=]\s*\S+|secret\s*[:=]\s*\S+)", "[secret]", cleaned)
    cleaned = cleaned.replace("\n", " ").replace("\r", " ").strip()
    return cleaned[:300] or "Document text extraction failed"
