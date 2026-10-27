"""灵枢 Agent 平台 —— 知识库路由。"""

import logging

import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.access import require_kb_write_access, require_workspace_kb
from api.deps import get_current_membership
from api.schemas import KnowledgeBaseCreateRequest, KnowledgeBaseUpdateRequest, KnowledgeDocumentCreateRequest
from core.db.models import KnowledgeBase, KnowledgeChunk, KnowledgeDocument, WorkspaceMember
from core.db.session import get_db
from core.services.knowledge import (
    KnowledgeDocumentError,
    add_document,
    chunk_document,
    create_chunk,
    create_knowledge_base,
    delete_chunk,
    delete_document,
    delete_knowledge_base,
    document_payload,
    extract_upload_text,
    index_document,
    knowledge_base_summary,
    list_document_chunks,
    mark_document_reindexing,
    run_document_ingestion,
    run_kb_reindex,
    update_chunk,
)
from core.services.rag import retrieve
from core.services.rag_cache import redis_store
from core.tasks.dispatch import dispatch

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge-bases", tags=["knowledge"])


class ResegmentRequest(BaseModel):
    # 注意：曾经有一个 `parse_mode` 字段，但它从未被任何代码读取（切分只认 segment_mode），
    # 前端也从未提交过它。已移除，避免调用方误以为“精确/快速解析”是可选行为。
    segment_mode: str = "auto"
    delimiter: str | None = "##"
    max_chunk_len: int = 5000
    overlap_pct: int = 10
    hierarchy_level: int = 3
    keep_hierarchy_info: bool = True


class DocumentMetadataRequest(BaseModel):
    """整份覆盖文档元数据；值一律按字符串存，过滤时也按字符串比较。"""
    metadata: dict[str, str] = Field(default_factory=dict)


class ChunkTextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


class PreviewUploadRequest(ResegmentRequest):
    """上传前预览：携带原始文件内容，提取文本后按策略在内存中切片，不落库。"""
    filename: str
    content_type: str = "application/octet-stream"
    content_base64: str


def _chunks_for_segment(text: str, *, kb_id: int, document_id: int, content_type: str, cfg: dict) -> list[dict]:
    """按 segment_mode 切片并返回 child 列表（预览专用，不落库）。

    直接复用入库分发器 chunk_document，保证「预览所见」与「实际入库切分」完全一致。
    """
    children, _ = chunk_document(
        text, content_type=content_type, kb_id=kb_id, document_id=document_id, segment_config=cfg,
    )
    return children


def _preview_payload(chunks: list[dict]) -> dict:
    return {
        "chunks_count": len(chunks),
        "preview_items": [
            {"chunk_index": idx, "text": chunk.get("text", ""), "hierarchy_path": chunk.get("section", "")}
            for idx, chunk in enumerate(chunks)
        ],
    }


@router.get("")
def list_knowledge_bases(membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    kbs = db.query(KnowledgeBase).filter(KnowledgeBase.workspace_id == membership.workspace_id).order_by(KnowledgeBase.id.desc()).all()
    # 🎯 N+1 优化：JOIN + GROUP BY 一次查询获取所有知识库的文档计数
    kb_ids = [kb.id for kb in kbs]
    counts: dict[int, int] = {}
    if kb_ids:
        rows = (
            db.query(
                KnowledgeDocument.knowledge_base_id,
                func.count(KnowledgeDocument.id),
            )
            .filter(KnowledgeDocument.knowledge_base_id.in_(kb_ids))
            .group_by(KnowledgeDocument.knowledge_base_id)
            .all()
        )
        counts = {kb_id: cnt for kb_id, cnt in rows}
    items = [knowledge_base_summary(kb, counts.get(kb.id, 0)) for kb in kbs]
    return {"items": items}


@router.post("")
def create_kb(request: KnowledgeBaseCreateRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    kb = create_knowledge_base(db, workspace_id=membership.workspace_id, user_id=membership.user_id, name=request.name, description=request.description)
    return {"knowledge_base": knowledge_base_summary(kb)}


@router.post("/{kb_id}/documents")
def upload_document(kb_id: int, request: KnowledgeDocumentCreateRequest, background_tasks: BackgroundTasks, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    kb = require_workspace_kb(db, membership.workspace_id, kb_id)
    require_kb_write_access(kb, membership)
    try:
        # defer_indexing：仅同步完成文本提取与落库，分块/向量化/落库交后台，避免阻塞上传请求。
        document = add_document(
            db, workspace_id=membership.workspace_id, kb=kb,
            filename=request.filename, title=request.title, text=request.text,
            content=request.content, content_type=request.content_type,
            content_base64=request.content_base64, source_type=request.source_type,
            segment_config=request.segment_config,
            defer_indexing=True,
        )
    except KnowledgeDocumentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Knowledge document upload failed")
        raise HTTPException(status_code=500, detail={"message": "Knowledge document upload failed.", "error_code": "knowledge_upload_failed"}) from exc
    payload = document_payload(document, 0)
    # 文本提取阶段即失败（坏文件等），直接返回 422，不调度后台入库。
    if document.status == "failed":
        raise HTTPException(status_code=422, detail={"message": document.error_message or "Document text extraction failed", "document": payload})
    dispatch(
        background_tasks,
        run_document_ingestion,
        task_name="lingshu.ingest_document",
        document_id=document.id, workspace_id=membership.workspace_id, kb_id=kb.id,
    )
    return {"document": payload}


@router.get("/{kb_id}/documents")
def list_documents(kb_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    kb = require_workspace_kb(db, membership.workspace_id, kb_id)
    documents = db.query(KnowledgeDocument).filter(KnowledgeDocument.knowledge_base_id == kb.id).order_by(KnowledgeDocument.id.desc()).all()
    return {
        "items": [
            document_payload(doc, db.query(KnowledgeChunk).filter(KnowledgeChunk.document_id == doc.id).count())
            for doc in documents
        ]
    }


@router.delete("/{kb_id}/documents/{document_id}")
def remove_document(kb_id: int, document_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    kb = require_workspace_kb(db, membership.workspace_id, kb_id)
    require_kb_write_access(kb, membership)
    document = db.query(KnowledgeDocument).filter(KnowledgeDocument.knowledge_base_id == kb.id, KnowledgeDocument.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    delete_document(db, workspace_id=membership.workspace_id, document=document)
    return {"deleted": True}


@router.post("/{kb_id}/index")
def index_kb(kb_id: int, background_tasks: BackgroundTasks, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    kb = require_workspace_kb(db, membership.workspace_id, kb_id)
    require_kb_write_access(kb, membership)
    # 重建整库索引耗时，移交后台；接口立即返回 running job，前端轮询 /knowledge/jobs/{job_id} 获取结果。
    job_id = f"kb-{kb.id}-{int(time.time())}"
    payload = {"job_id": job_id, "knowledge_base_id": kb.id, "status": "running", "message": "Knowledge base reindex started."}
    redis_store.set_job(job_id, payload)
    dispatch(
        background_tasks,
        run_kb_reindex,
        task_name="lingshu.reindex_kb",
        workspace_id=membership.workspace_id, kb_id=kb.id, job_id=job_id,
    )
    return payload


@router.post("/{kb_id}/documents/{document_id}/reindex")
def reindex_document(kb_id: int, document_id: int, background_tasks: BackgroundTasks,
                     membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    kb = require_workspace_kb(db, membership.workspace_id, kb_id)
    require_kb_write_access(kb, membership)
    document = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.knowledge_base_id == kb.id, KnowledgeDocument.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if not (document.text or "").strip():
        raise HTTPException(status_code=422, detail={"message": "文档无可索引文本，请重新上传文本版。"})
    if not mark_document_reindexing(db, document_id=document_id):
        raise HTTPException(status_code=409, detail={"message": "该文档正在索引中，请稍候。"})
    dispatch(background_tasks, run_document_ingestion, task_name="lingshu.ingest_document",
             document_id=document_id, workspace_id=membership.workspace_id, kb_id=kb.id)
    db.refresh(document)
    return {"document": document_payload(document, 0)}


@router.get("/{kb_id}/documents/{document_id}/chunks")
def get_document_chunks(kb_id: int, document_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    kb = require_workspace_kb(db, membership.workspace_id, kb_id)
    document = db.query(KnowledgeDocument).filter(KnowledgeDocument.knowledge_base_id == kb.id, KnowledgeDocument.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    chunks = list_document_chunks(db, document_id=document.id)
    return {"document": document_payload(document, len(chunks)), "chunks": chunks}


def _require_document(db: Session, kb: KnowledgeBase, document_id: int) -> KnowledgeDocument:
    document = db.query(KnowledgeDocument).filter(KnowledgeDocument.knowledge_base_id == kb.id, KnowledgeDocument.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


def _require_chunk(db: Session, document: KnowledgeDocument, chunk_id: int) -> KnowledgeChunk:
    chunk = db.query(KnowledgeChunk).filter(KnowledgeChunk.document_id == document.id, KnowledgeChunk.id == chunk_id).first()
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return chunk


def _chunk_response(db: Session, document: KnowledgeDocument) -> dict:
    chunks = list_document_chunks(db, document_id=document.id)
    return {"document": document_payload(document, len(chunks)), "chunks": chunks}


@router.put("/{kb_id}/documents/{document_id}/metadata")
def put_document_metadata(kb_id: int, document_id: int, request: DocumentMetadataRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    """给文档打元数据（产品、版本、部门…），检索时可按它圈定范围。"""
    kb = require_workspace_kb(db, membership.workspace_id, kb_id)
    require_kb_write_access(kb, membership)
    document = _require_document(db, kb, document_id)
    cleaned = {key.strip()[:64]: str(value).strip()[:255] for key, value in request.metadata.items() if key.strip()}
    if len(cleaned) > 20:
        raise HTTPException(status_code=400, detail="每篇文档最多 20 个元数据字段")
    document.doc_metadata = cleaned
    db.commit()
    db.refresh(document)
    return {"document": document_payload(document)}


@router.post("/{kb_id}/documents/{document_id}/chunks")
def create_document_chunk(kb_id: int, document_id: int, request: ChunkTextRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    """补一条切片：文档漏掉的关键内容直接补进去，不用改原文再重切。"""
    kb = require_workspace_kb(db, membership.workspace_id, kb_id)
    require_kb_write_access(kb, membership)
    document = _require_document(db, kb, document_id)
    try:
        create_chunk(db, document=document, text=request.text)
    except KnowledgeDocumentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return _chunk_response(db, document)


@router.patch("/{kb_id}/documents/{document_id}/chunks/{chunk_id}")
def update_document_chunk(kb_id: int, document_id: int, chunk_id: int, request: ChunkTextRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    """改一条切片：重新向量化并刷新 BM25 词表，下次检索立刻生效。"""
    kb = require_workspace_kb(db, membership.workspace_id, kb_id)
    require_kb_write_access(kb, membership)
    document = _require_document(db, kb, document_id)
    chunk = _require_chunk(db, document, chunk_id)
    try:
        update_chunk(db, document=document, chunk=chunk, text=request.text)
    except KnowledgeDocumentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return _chunk_response(db, document)


@router.delete("/{kb_id}/documents/{document_id}/chunks/{chunk_id}")
def delete_document_chunk(kb_id: int, document_id: int, chunk_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    kb = require_workspace_kb(db, membership.workspace_id, kb_id)
    require_kb_write_access(kb, membership)
    document = _require_document(db, kb, document_id)
    delete_chunk(db, document=document, chunk=_require_chunk(db, document, chunk_id))
    return _chunk_response(db, document)


@router.post("/{kb_id}/documents/{document_id}/preview")
def preview_document_chunks(kb_id: int, document_id: int, request: ResegmentRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    kb = require_workspace_kb(db, membership.workspace_id, kb_id)
    document = db.query(KnowledgeDocument).filter(KnowledgeDocument.knowledge_base_id == kb.id, KnowledgeDocument.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    chunks = _chunks_for_segment(
        document.text, kb_id=kb.id, document_id=document.id,
        content_type=document.content_type, cfg=request.model_dump(),
    )
    return _preview_payload(chunks)


@router.post("/{kb_id}/documents/preview-upload")
def preview_upload_chunks(kb_id: int, request: PreviewUploadRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    """上传前预览：对原始文件提取文本并按策略切片，不落库、不嵌入。"""
    kb = require_workspace_kb(db, membership.workspace_id, kb_id)
    try:
        text = extract_upload_text(filename=request.filename, content_type=request.content_type, content_base64=request.content_base64)
    except KnowledgeDocumentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    chunks = _chunks_for_segment(
        text, kb_id=kb.id, document_id=0,
        content_type=request.content_type, cfg=request.model_dump(),
    )
    return _preview_payload(chunks)


@router.post("/{kb_id}/documents/{document_id}/resegment")
def resegment_document_chunks(kb_id: int, document_id: int, request: ResegmentRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    kb = require_workspace_kb(db, membership.workspace_id, kb_id)
    require_kb_write_access(kb, membership)
    document = db.query(KnowledgeDocument).filter(KnowledgeDocument.knowledge_base_id == kb.id, KnowledgeDocument.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    document.segment_config = request.model_dump()
    db.commit()
    try:
        chunk_count = index_document(db, workspace_id=membership.workspace_id, kb=kb, document=document, clear_existing=True)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail={"message": f"Resegment index failed: {str(exc)}"})
    return {"document": document_payload(document, chunk_count)}


@router.delete("/{kb_id}")
def delete_kb(kb_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    kb = require_workspace_kb(db, membership.workspace_id, kb_id)
    require_kb_write_access(kb, membership)
    delete_knowledge_base(db, workspace_id=membership.workspace_id, kb=kb)
    return {"deleted": True}


@router.patch("/{kb_id}")
def update_kb(kb_id: int, request: KnowledgeBaseUpdateRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    kb = require_workspace_kb(db, membership.workspace_id, kb_id)
    require_kb_write_access(kb, membership)
    # 🔁 局部更新：只改显式提供的字段，未提供者保持原值（改名/改描述/启停可独立调用）。
    if request.name is not None:
        # min_length=1 已拦空串；此处再拦纯空白（"   " 长度为 3，能过 pydantic 但过不了 strip），
        # 并以 strip 后的值入库，避免首尾空白污染展示与检索。
        if not request.name.strip():
            raise HTTPException(status_code=422, detail="Knowledge base name must not be empty")
        kb.name = request.name.strip()
    if request.description is not None:
        kb.description = request.description
    if request.enabled is not None:
        kb.enabled = bool(request.enabled)
    db.commit()
    db.refresh(kb)
    return {"knowledge_base": knowledge_base_summary(kb)}


class RetrievalTestRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=6, ge=1, le=20)
    rerank_enabled: bool = True
    metadata_filter: dict | None = None



@router.post("/{kb_id}/retrieval-test")
def retrieval_test(kb_id: int, request: RetrievalTestRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    """
    召回测试：对单个知识库跑一遍真实检索链路，返回每个片段在各通道的分数。

    🎯 用于调 RAG：看某个问题到底能不能命中、命中的是哪段、是向量还是关键词命中的、
        rerank 把它排到了第几。走的是和对话完全相同的 retrieve()，所见即对话时所得。
    🛡️ 关闭缓存：调参时要看实时结果，命中旧缓存会让刚改的分块/文档看起来没生效。
        停用的知识库直接返回 reason=all_knowledge_bases_disabled，与对话时的行为一致。
    """
    kb = require_workspace_kb(db, membership.workspace_id, kb_id)
    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=422, detail="请输入测试问题")
    config = {
        "top_k": request.top_k,
        "rerank_enabled": request.rerank_enabled,
        "cache_enabled": False,
        "parent_expansion": False,
    }
    if request.metadata_filter:
        config["metadata_filter"] = request.metadata_filter
    started = time.monotonic()
    result = retrieve(db, workspace_id=membership.workspace_id, knowledge_base_ids=[kb.id], query=query, config=config)
    status = result.status
    return {
        "query": query,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "status": {
            "reason": status.get("reason"),
            "dense": status.get("dense"),
            "bm25": status.get("bm25"),
            "rrf": status.get("rrf"),
            "rerank": status.get("rerank"),
            "translate": status.get("translate"),
            "no_evidence": status.get("no_evidence"),
            "metadata_filter": status.get("metadata_filter"),
        },
        "items": [
            {
                "chunk_id": item.get("chunk_id"),
                "document_id": item.get("document_id"),
                "title": item.get("title"),
                "section": item.get("section"),
                "page": item.get("page"),
                "snippet": item.get("content") or item.get("snippet"),
                "score": item.get("score"),
                "dense_score": item.get("dense_score"),
                "bm25_score": item.get("bm25_score"),
                "channel": item.get("retrieval_channel"),
            }
            for item in result.sources
        ],
    }
