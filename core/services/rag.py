from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.config import get_settings
from core.db.models import KnowledgeChunk, KnowledgeDocument, KnowledgeParentChunk
from core.integrations.llm import OpenAICompatibleProvider
from core.integrations import vector_store as vector_store_module
from core.observability.metrics import record_cache
from core.services.rag_cache import redis_store, ttl_with_jitter
from core.services.query_translate import translate_query

# 🧠 魔鬼数字：BM25 构建分批批次大小，防止全表读取导致的高内存开销与游标挂起
BM25_BATCH_SIZE = 1000

# 🎯 线程安全的全局内存缓存（BM25 索引热加载缓存器）
# 物理哈希格式 Key: (frozenset(knowledge_base_ids), version_hash) -> Value: (compiled_bm25_index, rows_data)
# 意图说明：
# BM25 计算在大文档量时极其昂贵。我们在内存缓存已编译好的 BM25Index。
# 利用 `version_hash`（由知识库中所有文档的最后修改时间 + 数量组合而成）作为缓存键的一部分，
# 一旦有文档增删改，哈希自然改变，旧缓存失效，新索引自动懒构建，实现开箱即用的近实时“实时索引更新”。
_BM25_INDEX_CACHE = {}
_BM25_CACHE_LOCK = threading.Lock()


@dataclass
class RagResult:
    """
    RAG 混合检索出的最终融合包体。
    """
    sources: list[dict]
    status: dict


def retrieve(
    db: Session,
    *,
    workspace_id: int,
    knowledge_base_ids: list[int],
    query: str,
    config: dict,
    runtime_config: dict | None = None,
) -> RagResult:
    """
    RAG 混合检索编排核心（Retrieval Orchestrator）。

    🎯 意图与工程大局观：
        本方法是整个平台检索增强生成的核心。
        检索流水线设计如下：
        1. 租户隔离检测与前置 Redis 缓存嗅探（若命中则直接返回）。
        2. 发起向量化 Dense Search（稠密向量相似度检索）以获取语义近似碎片。
        3. 发起本地 BM25 Search（倒排词频检索）以获取精准术语匹配。
        4. 使用互惠排名融合（RRF, Reciprocal Rank Fusion）抹平两个不同算分模型的量纲，生成全局排序。
        5. 调用深度语义 Rerank 重排模型（若启用）重新评估 Query 与 Document 的相关度。
        6. 进行“证据度校验（_has_evidence）”判定是否命中真实证据，防止无关闲聊被强制套上知识库导致幻觉。
        7. 回写 Redis 缓存，输出最终 Sources 片段。
    """
    settings = get_settings()
    started_status = _base_status(query, knowledge_base_ids, config)
    if not knowledge_base_ids:
        started_status.update({"reason": "no_knowledge_base", "no_evidence": True})
        return RagResult([], started_status)

    # 🎯 缓存防雪崩与击穿：使用结合了 Query、Config 和文档最后修改时间戳的版本化 `cache_key`
    cache_key = _cache_key(db, workspace_id=workspace_id, knowledge_base_ids=knowledge_base_ids, query=query, config=config)
    if config.get("cache_enabled", True):
        cached = redis_store.get_json(cache_key)
        record_cache("rag_result", bool(cached.hit and cached.value))
        if cached.hit and cached.value:
            status = cached.value.get("status", {})
            status.update({"cache": {"enabled": True, "hit": True, "backend": cached.backend}})
            return RagResult(cached.value.get("sources", []), status)

    # 1. 稠密向量搜索通道 (Dense Channel)
    provider = OpenAICompatibleProvider()
    query_vector = provider.embed(query, runtime_config=runtime_config)
    dense_hits = _dense_search(
        workspace_id=workspace_id,
        knowledge_base_ids=knowledge_base_ids,
        query_vector=query_vector,
        limit=int(config.get("dense_top_k") or settings.rag_dense_top_k),
    )
    
    # 2. 稀疏文本搜索通道 (Sparse BM25 Channel)
    # 🌐 跨语言查询翻译：中文查询→英文翻译喂给 BM25（英文文档），修复跨语言词项零重叠
    translated_query = translate_query(provider, query, runtime_config=runtime_config)
    bm25_query = translated_query or query
    bm25_hits = _bm25_search(
        db,
        workspace_id=workspace_id,
        knowledge_base_ids=knowledge_base_ids,
        query=bm25_query,
        limit=int(config.get("bm25_top_k") or settings.rag_bm25_top_k),
    )
    
    # 3. 多路互惠排名融合 (Reciprocal Rank Fusion)
    fused_hits = _rrf(dense_hits, bm25_hits, k=int(config.get("rrf_k") or settings.rag_rrf_k))
    
    # 4. 精准语义重排 (Rerank Phase)
    # 🛡️ 容错设计：Rerank 通常涉及第三方外接服务（可能遭遇并发率受限或超时崩溃），
    # 在 try-catch 中进行包裹，一旦 Rerank 出错，降级到直接输出 RRF 融合排序，决不让 Rerank 异常拖垮主流程。
    rerank_applied = False
    rerank_error = ""
    final_hits = fused_hits
    if config.get("rerank_enabled", settings.rag_rerank_enabled) and fused_hits:
        try:
            final_hits = _rerank(
                provider,
                query=query,
                hits=fused_hits,
                top_n=int(config.get("rerank_top_n") or settings.rag_rerank_top_n),
                model=settings.rag_rerank_model,
            )
            rerank_applied = True
        except Exception as exc:
            rerank_error = str(exc)[:240]
            final_hits = fused_hits

    # 5. 组装输出与有效证据评估
    top_k = int(config.get("top_k") or settings.rag_top_k)
    sources = [_source_payload(hit) for hit in final_hits[:top_k]]

    # 5.5 父块扩展（small-to-big）：child 已用于精准命中+排序，喂给 LLM 的正文换成父块全文。
    # 🛡️ 容错：父块查询失败/缺失绝不拖垮主流程，content 自动保留 child 全文兜底。
    parent_expansion = bool(config.get("parent_expansion", settings.rag_parent_expansion))
    if parent_expansion and sources:
        _expand_parents(db, workspace_id=workspace_id, sources=sources)

    no_evidence = not _has_evidence(sources, bm25_query)

    status = started_status | {
        "reason": "available" if sources else "no_match",
        "matched_chunks": len(final_hits),
        "sources_emitted": bool(sources),
        "dense": {"top_k": int(config.get("dense_top_k") or settings.rag_dense_top_k), "matched": len(dense_hits)},
        "bm25": {"top_k": int(config.get("bm25_top_k") or settings.rag_bm25_top_k), "matched": len(bm25_hits)},
        "rrf": {"k": int(config.get("rrf_k") or settings.rag_rrf_k), "matched": len(fused_hits)},
        "rerank": {
            "enabled": bool(config.get("rerank_enabled", settings.rag_rerank_enabled)),
            "applied": rerank_applied,
            "model": settings.rag_rerank_model,
            "error": rerank_error or None,
        },
        "translate": {"enabled": settings.translate_query_enabled, "applied": translated_query is not None, "model": settings.translate_model or settings.openai_model},
        "cache": {"enabled": bool(config.get("cache_enabled", settings.rag_cache_enabled)), "hit": False, "backend": "redis" if redis_store.available else "none"},
        "no_evidence": no_evidence,
        "refuse_when_no_evidence": bool(config.get("refuse_when_no_evidence", settings.rag_refuse_when_no_evidence)),
        "rag_model": "environment",
    }
    
    # 6. 回写 Redis（TTL 带抖动防雪崩：版本化 key 在文档更新后会让一批缓存同时失效）
    if config.get("cache_enabled", True):
        redis_store.set_json(cache_key, {"sources": sources, "status": status}, ttl_with_jitter(settings.rag_cache_ttl_seconds))
    return RagResult(sources, status)


def _base_status(query: str, knowledge_base_ids: list[int], config: dict) -> dict:
    """初始化 RAG trace 状态结构。"""
    settings = get_settings()
    return {
        "enabled": True,
        "knowledge_base_ids": knowledge_base_ids,
        "query": query,
        "top_k": int(config.get("top_k") or settings.rag_top_k),
        "matched_chunks": 0,
        "sources_emitted": False,
        "reason": "started",
    }


def _dense_search(*, workspace_id: int, knowledge_base_ids: list[int], query_vector: list[float], limit: int) -> list[dict]:
    """
    向量稠密检索。
    """
    hits = []
    for kb_id in knowledge_base_ids:
        for hit in vector_store_module.vector_store.search(
            query_vector,
            limit=limit,
            filters={"workspace_id": workspace_id, "knowledge_base_id": kb_id},
        ):
            metadata = hit.metadata or {}
            hits.append(
                {
                    "id": metadata.get("chunk_id") or hit.vector_id,
                    "vector_id": hit.vector_id,
                    "text": hit.text,
                    "score": float(hit.score),
                    "dense_score": float(hit.score),
                    "retrieval_channel": "dense",
                    "metadata": metadata,
                }
            )
    return sorted(hits, key=lambda item: item["score"], reverse=True)[:limit]


def _bm25_search(db: Session, *, workspace_id: int, knowledge_base_ids: list[int], query: str, limit: int) -> list[dict]:
    """
    内存倒排 BM25 检索核心（支持自适应自举编译索引）。

    ⚡ 边界与性能思考（大内存优化策略）：
        - 对大规模数据库分块记录，如果直接一次性 `.all()` 调入，会造成严重的物理内存暴涨以及 SQLAlchemy 对象跟踪开销。
        - 采取批处理分片读取机制（`offset().limit()`），分批提取，并配以 `db.expire_all()`。
          `db.expire_all()` 极其关键，它用于主动释放 SQLAlchemy 内部的 Session 一级实体缓存，防止读入十万级分块直接耗尽 JVM/Python 堆内存。
        - 对分词操作，优先使用高性能结巴（`jieba`）库进行中文精确分词；若缺失结巴环境，自动防御退化为正则表达式匹配非空 Unicode 汉字和单词列表。
    """
    # 🎯 生成版本控制特征指纹：检查关联知识库内所有文件的个数和最大修改时间戳
    stats = (
        db.query(
            KnowledgeDocument.knowledge_base_id,
            func.count(KnowledgeDocument.id),
            func.max(KnowledgeDocument.updated_at),
        )
        .filter(KnowledgeDocument.knowledge_base_id.in_(knowledge_base_ids))
        .group_by(KnowledgeDocument.knowledge_base_id)
        .all()
    )
    parts = [f"{kb_id}:{count}:{max_updated.isoformat() if max_updated else '0'}" for kb_id, count, max_updated in sorted(stats, key=lambda x: x[0])]
    version = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    
    cache_key = (frozenset(knowledge_base_ids), version)
    
    # 🎯 线程安全的缓存拉取：防止并发访问临界区引起死锁或缓存状态错乱
    with _BM25_CACHE_LOCK:
        cached_data = _BM25_INDEX_CACHE.get(cache_key)
        
    if cached_data:
        bm25_index, rows_data = cached_data
    else:
        # 未命中缓存，开始懒编译 BM25 索引
        query_chunks = (
            db.query(KnowledgeChunk)
            .filter(
                KnowledgeChunk.workspace_id == workspace_id,
                KnowledgeChunk.knowledge_base_id.in_(knowledge_base_ids)
            )
        )
        total_count = query_chunks.count()
        if total_count == 0:
            return []
            
        rows_data = []
        tokenized_corpus = []
        
        # ⚡ 深度内存节约：批式分片检索与垃圾主动回收
        for offset in range(0, total_count, BM25_BATCH_SIZE):
            batch = query_chunks.order_by(KnowledgeChunk.id.asc()).offset(offset).limit(BM25_BATCH_SIZE).all()
            for row in batch:
                meta = row.metadata_ or {}
                tokens = meta.get("tokens")
                if tokens is None:
                    tokens = _tokenize(row.text)
                tokenized_corpus.append(tokens)
                rows_data.append({
                    "id": row.chunk_id or row.vector_id,
                    "vector_id": row.vector_id,
                    "text": row.text,
                    "metadata": _row_metadata(row),
                    "tokens": tokens
                })
        # ⚡ 批量加载完成后统一释放 SQLAlchemy 会话状态，避免循环内逐批 expire 导致的重复查询
            
        # 编译倒排表索引
        try:
            from rank_bm25 import BM25Okapi
            bm25_index = BM25Okapi(tokenized_corpus)
        except Exception:
            bm25_index = None
            
        with _BM25_CACHE_LOCK:
            # 🛡️ 内存控制策略：缓存长度硬限制 100，超出自动全清空重建，杜绝因知识库数量过多造成的内存虚高
            if len(_BM25_INDEX_CACHE) > 100:
                _BM25_INDEX_CACHE.clear()
            _BM25_INDEX_CACHE[cache_key] = (bm25_index, rows_data)
    
    tokenized_query = _tokenize(query)
    
    # 计算词频分值
    if bm25_index:
        try:
            scores = [float(s) for s in bm25_index.get_scores(tokenized_query)]
        except Exception:
            scores = _bm25_scores([r["tokens"] for r in rows_data], tokenized_query)
    else:
        scores = _bm25_scores([r["tokens"] for r in rows_data], tokenized_query)
        
    hits = []
    query_token_set = set(tokenized_query)
    
    for data, score in zip(rows_data, scores):
        row_tokens = data["tokens"]
        overlap = query_token_set.intersection(row_tokens)
        # 🛡️ 短路匹配：如果无词频分数且连字面交集也无，跳过，提升最终多通道过滤性能
        if score <= 0 and tokenized_query and not overlap:
            continue
        if score <= 0 and overlap:
            score = len(overlap) / max(len(query_token_set), 1)
        hits.append(
            {
                "id": data["id"],
                "vector_id": data["vector_id"],
                "text": data["text"],
                "score": float(score),
                "bm25_score": float(score),
                "retrieval_channel": "bm25",
                "metadata": data["metadata"],
            }
        )
    return sorted(hits, key=lambda item: item["score"], reverse=True)[:limit]


def _rrf(dense_hits: list[dict], bm25_hits: list[dict], *, k: int) -> list[dict]:
    """
    互惠排名融合算法 (Reciprocal Rank Fusion)。

    🎯 意图与工程大局观：
        抹平不同计算模型（例如 Milvus Cosine 相似度为 [-1.0, 1.0]，而 BM25 理论分值为无界正实数 [0, +inf]）的基准尺度。
        计算公式： RRF_Score = Sum( 1 / (k + rank_channel) )
        k 默认为 60（业界公认的经验常数最优值），有效防范头部排序结果的轻微排序浮动被无限制放大。
    """
    combined: dict[str, dict] = {}
    for channel, hits in (("dense", dense_hits), ("bm25", bm25_hits)):
        for rank, hit in enumerate(hits, start=1):
            key = hit["id"]
            item = combined.setdefault(key, {**hit, "score": 0.0, "channels": set()})
            item["score"] += 1 / (k + rank)
            item["channels"].add(channel)
            if channel == "dense":
                item["dense_score"] = hit.get("dense_score", hit.get("score", 0.0))
            if channel == "bm25":
                item["bm25_score"] = hit.get("bm25_score", hit.get("score", 0.0))
    results = []
    for item in combined.values():
        channels = sorted(item.pop("channels", []))
        item["retrieval_channel"] = "rrf:" + "+".join(channels)
        results.append(item)
    return sorted(results, key=lambda item: item["score"], reverse=True)


def _rerank(provider: OpenAICompatibleProvider, *, query: str, hits: list[dict], top_n: int, model: str) -> list[dict]:
    """
    Rerank 深度多阶段排序。
    """
    documents = [hit["text"] for hit in hits]
    ranked = provider.rerank(query, documents, top_n=min(top_n, len(documents)), model=model)
    output = []
    used = set()
    for item in ranked:
        index = item["index"]
        if index < 0 or index >= len(hits) or index in used:
            continue
        used.add(index)
        hit = {**hits[index]}
        hit["score"] = float(item.get("relevance_score", hit.get("score", 0)))
        hit["retrieval_channel"] = "rerank"
        output.append(hit)
    # 🛡️ 兜底防护：如果有部分未被重排的落选片段，按原序置于队尾，防止在切片时彻底丢失备用上下文
    output.extend(hit for index, hit in enumerate(hits) if index not in used)
    return output


def _source_payload(hit: dict) -> dict:
    """转换召回信息为前端强类型来源字典。"""
    metadata = hit.get("metadata") or {}
    title = metadata.get("title") or metadata.get("filename") or f"document-{metadata.get('document_id', '')}".strip("-")
    return {
        "source_id": title,
        "document_id": metadata.get("document_id"),
        "chunk_id": metadata.get("chunk_id") or hit.get("id"),
        "parent_id": metadata.get("parent_id") or metadata.get("chunk_id") or hit.get("id"),
        "title": title or "knowledge",
        "page": metadata.get("page"),
        "section": metadata.get("section") or "",
        "snippet": (hit.get("text") or "")[:360],
        # content：喂给 LLM 的正文。默认 = child 全文（不截断）；父块扩展命中时被父块全文覆盖。
        "content": hit.get("text") or "",
        "score": float(hit.get("score") or 0),
        "dense_score": float(hit.get("dense_score") or 0),
        "bm25_score": float(hit.get("bm25_score") or 0),
        "retrieval_channel": hit.get("retrieval_channel") or "dense",
    }


def _expand_parents(db: Session, *, workspace_id: int, sources: list[dict]) -> None:
    """
    父块扩展（small-to-big 检索）：就地把每个 source 的 `content` 替换为其父块全文。

    🎯 意图与工程大局观：
        child 小块利于向量/BM25 精准命中与排序，但喂给 LLM 时上下文太碎、易割裂语义。
        本方法在排序定稿后，按 child 的 parent_id 批量回捞 KnowledgeParentChunk 全文，
        以「更大的连续上下文」替换喂给模型的正文，命中精度与上下文完整性兼得。
        `snippet`（child 截断）保持不动，继续用于前端引用展示与 _has_evidence 字面证据校验。

    🛡️ 防御性设计：
        - parent_id 形如 `kb{kb}-doc{doc}-parent{n}`，全局唯一，按 workspace 隔离批量 IN 查询，单次往返。
        - 任意异常（DB 抖动等）一律吞掉，content 保留 child 全文兜底，绝不拖垮检索主流程。
        - 旧数据/非层级切分无对应父块行时，对应 source 静默保留 child 全文。
    """
    parent_ids = {src.get("parent_id") for src in sources if src.get("parent_id")}
    if not parent_ids:
        return
    try:
        rows = (
            db.query(KnowledgeParentChunk.parent_id, KnowledgeParentChunk.text)
            .filter(
                KnowledgeParentChunk.workspace_id == workspace_id,
                KnowledgeParentChunk.parent_id.in_(parent_ids),
            )
            .all()
        )
    except Exception:
        return
    parent_text = {parent_id: text for parent_id, text in rows if text}
    for src in sources:
        text = parent_text.get(src.get("parent_id"))
        if text:
            src["content"] = text


def _row_metadata(row: KnowledgeChunk) -> dict:
    """打包映射字段。"""
    metadata = dict(row.metadata_ or {})
    metadata.update(
        {
            "workspace_id": row.workspace_id,
            "knowledge_base_id": row.knowledge_base_id,
            "document_id": row.document_id,
            "chunk_id": row.chunk_id or row.vector_id,
            "parent_id": row.parent_id or row.vector_id,
            "title": row.title,
            "page": row.page,
            "section": row.section,
            "content_hash": row.content_hash,
        }
    )
    return metadata


def _tokenize(text: str) -> list[str]:
    """高性能中英文分词器。"""
    try:
        import jieba

        return [token.strip().lower() for token in jieba.lcut(text) if token.strip()]
    except Exception:
        return [token.lower() for token in re.findall(r"[\w\u4e00-\u9fff]+", text)]


def _bm25_scores(corpus: list[list[str]], query_tokens: list[str]) -> list[float]:
    """
    防灾退化版原生的 BM25 算分公式（在未安装第三方库时的防御实现）。

    🧠 核心数学常数设计：
        - k1 = 1.5: 调节词频饱和度（Term Frequency Saturation）。值越大，词频贡献增长越慢。
        - b = 0.75: 文档长度惩罚系数（Document Length Penalty）。值越大，对超长篇幅文本块的惩罚力度越强。
    """
    if not corpus or not query_tokens:
        return [0.0 for _ in corpus]
    try:
        from rank_bm25 import BM25Okapi

        return [float(score) for score in BM25Okapi(corpus).get_scores(query_tokens)]
    except Exception:
        doc_count = len(corpus)
        avg_len = sum(len(doc) for doc in corpus) / max(doc_count, 1)
        doc_freq = {}
        for doc in corpus:
            for token in set(doc):
                doc_freq[token] = doc_freq.get(token, 0) + 1
        scores = []
        for doc in corpus:
            score = 0.0
            length = len(doc) or 1
            for token in query_tokens:
                freq = doc.count(token)
                if not freq:
                    continue
                # 计算标准的 IDF（逆文档频率）分值，附加 0.5 顺滑因子避开零频冲突
                idf = math.log((doc_count - doc_freq.get(token, 0) + 0.5) / (doc_freq.get(token, 0) + 0.5) + 1)
                denom = freq + 1.5 * (1 - 0.75 + 0.75 * length / max(avg_len, 1))
                score += idf * (freq * 2.5 / denom)
            scores.append(score)
        return scores


# 🎯 坚固的 RAG 拒答词过滤机制。
# 阻止那些无实质含义的问候句、语气助词或系统指代词（如“帮我”、“你好”）无端激活 RAG 匹配分，防止把无关的随机向量碎片灌给 LLM 导致胡言乱语。
RAG_CONVERSATIONAL_STOPWORDS = {
    "这个", "那个", "能够", "可以", "能够", "帮我", "做什么", "做点什么", 
    "功能", "介绍", "自己", "是谁", "谁是", "你好", "您好", "怎么", "如何", 
    "什么", "智能体", "机器人", "助手", "客服", "智能", "系统", "功能", 
    "回答", "问题", "帮我", "谢谢", "再见", "请问", "关于", "内容", "我们",
    "你们", "他们", "它们", "什么样", "哪些", "哪个", "帮助"
}


def _has_evidence(sources: list[dict], query: str) -> bool:
    """
    RAG 核心守门人：判断召回是否属于“有效证据”。

    🛡️ 防御性设计：
        - 为什么要做证据判定？
          如果用户发“早上好！”，向量检索会因为密度的余弦相似度计算特征，强制返回一篇含有“早上”或字面上最接近的完全不相干的文章作为上下文。
          这会诱发严重的 LLM 生成幻觉（大模型被喂入无意义文章后，可能会强行把问候语同产品说明文缝合在一起）。
        
    🧠 判断阈值逻辑：
        1. 前置清扫过滤停用词，检查 Query 的实质词是否真的包含在返回的 Snippet 原文中。
        2. 如果词汇层面无交集，严格检查三大通道的评分底线：
           - BM25 Score > 0 （确认存在精确的分词交叠）。
           - Dense Channel 余弦分值 >= 0.40 （向量距离底线线）。
           - Rerank 重排打分值 >= 0.40 （高保真重排重估得分阈值）。
           一旦均低于上述底线，本方法将返回 `False` 触发拒答或使用大模型本能兜底。
    """
    if not sources:
        return False
        
    tokens = [
        token for token in _tokenize(query) 
        if len(token) > 1 and token.lower() not in RAG_CONVERSATIONAL_STOPWORDS
    ]
    
    snippets = " ".join(source.get("snippet", "") for source in sources).lower()
    
    if tokens and any(token.lower() in snippets for token in tokens):
        return True
        
    for source in sources:
        channel = source.get("retrieval_channel", "")
        if "bm25" in channel and source.get("bm25_score", 0.0) > 0:
            return True
        if "dense" in channel and source.get("dense_score", 0.0) >= 0.40:
            return True
        if "rerank" in channel and (source.get("score", 0.0) >= 0.40 or source.get("dense_score", 0.0) >= 0.40):
            return True
            
    return False


def _cache_key(db: Session, *, workspace_id: int, knowledge_base_ids: list[int], query: str, config: dict) -> str:
    """
    生成 RAG 独一无二的自适应缓存键。
    
    ⚡ 边界与性能思考：
        - 对检索的 Query 进行空白压缩、首尾剔除与小写化规整，让逻辑一致但格式微小的不同 Query 能够复用同一份 RAG 缓存。
        - 缓存键包含基于文档元数据时间戳演进的 `version` 值。这意味着只要数据库底层任何一篇知识文档发生过修改，生成的缓存键必定彻底失效，确保缓存永远为新鲜热数据，拒绝任何“脏数据”重放。
    """
    stats = (
        db.query(
            KnowledgeDocument.knowledge_base_id,
            func.count(KnowledgeDocument.id),
            func.max(KnowledgeDocument.updated_at),
        )
        .filter(KnowledgeDocument.knowledge_base_id.in_(knowledge_base_ids))
        .group_by(KnowledgeDocument.knowledge_base_id)
        .all()
    )
    parts = [f"{kb_id}:{count}:{max_updated.isoformat() if max_updated else '0'}" for kb_id, count, max_updated in sorted(stats, key=lambda x: x[0])]
    version = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    payload = {
        "workspace_id": workspace_id,
        "knowledge_base_ids": sorted(knowledge_base_ids),
        "query": re.sub(r"\s+", " ", query).strip().lower(),
        "version": version,
        "config": config,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return f"rag:{digest}"
