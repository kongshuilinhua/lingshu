import json

from core.services.agents import normalize_rag
from core.services.knowledge import split_parent_child
from core.services.rag import _rrf


def _events(body: str, event_name: str) -> list[dict]:
    items = []
    event = None
    data = []
    for line in body.splitlines():
        if line.startswith("event: "):
            event = line.removeprefix("event: ").strip()
            data = []
        elif line.startswith("data: "):
            data.append(line.removeprefix("data: "))
        elif not line:
            if event == event_name and data:
                items.append(json.loads("\n".join(data)))
            event = None
            data = []
    return items


def test_normalize_rag_uses_final_config_defaults(monkeypatch):
    monkeypatch.setenv("RAG_TOP_K", "5")
    monkeypatch.setenv("RAG_DENSE_TOP_K", "13")
    monkeypatch.setenv("RAG_BM25_TOP_K", "11")
    monkeypatch.setenv("RAG_RRF_K", "55")
    monkeypatch.setenv("RAG_RERANK_ENABLED", "false")
    from core.config import get_settings

    get_settings.cache_clear()
    try:
        config = normalize_rag({})
    finally:
        get_settings.cache_clear()

    assert config["top_k"] == 5
    assert config["dense_top_k"] == 13
    assert config["bm25_top_k"] == 11
    assert config["rrf_k"] == 55
    assert config["rerank_enabled"] is False


def test_parent_child_chunk_ids_are_stable():
    chunks = split_parent_child("alpha " * 400, kb_id=1, document_id=2, parent_size=200, child_size=80, overlap=10)

    assert chunks
    assert chunks[0]["parent_id"] == "kb1-doc2-parent0"
    assert chunks[0]["chunk_id"].startswith("kb1-doc2-parent0-child")
    assert chunks[0]["content_hash"]
    assert any(item["parent_id"] != chunks[0]["parent_id"] for item in chunks)


def test_rrf_merges_dense_and_bm25_channels():
    dense = [{"id": "a", "text": "A", "score": 0.9, "metadata": {}, "retrieval_channel": "dense"}]
    bm25 = [{"id": "a", "text": "A", "score": 3.0, "metadata": {}, "retrieval_channel": "bm25"}]

    fused = _rrf(dense, bm25, k=60)

    assert fused[0]["id"] == "a"
    assert fused[0]["retrieval_channel"] == "rrf:bm25+dense"


class _FakeParentQuery:
    """最小化模拟 db.query(...).filter(...).all()，返回预置 (parent_id, text) 行。"""

    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self._rows


class _FakeParentDB:
    def __init__(self, rows):
        self._rows = rows

    def query(self, *args, **kwargs):
        return _FakeParentQuery(self._rows)


def test_expand_parents_overrides_content_with_parent_text():
    from core.services.rag import _expand_parents

    sources = [
        {"parent_id": "kb1-doc1-parent0", "snippet": "child A 截断", "content": "child A 全文"},
        {"parent_id": "kb1-doc1-parent0", "snippet": "child B 截断", "content": "child B 全文"},
        {"parent_id": "kb1-doc1-parent9", "snippet": "孤儿 child 截断", "content": "孤儿 child 全文"},
    ]
    db = _FakeParentDB([("kb1-doc1-parent0", "父块0的完整连续上下文")])

    _expand_parents(db, workspace_id=1, sources=sources)

    # 命中父块的 child：content 被父块全文覆盖，snippet 保持不变（仍用于引用展示与证据校验）
    assert sources[0]["content"] == "父块0的完整连续上下文"
    assert sources[0]["snippet"] == "child A 截断"
    assert sources[1]["content"] == "父块0的完整连续上下文"
    # 无对应父块行（旧数据/非层级切分）：静默回退到 child 全文
    assert sources[2]["content"] == "孤儿 child 全文"


def test_expand_parents_tolerates_db_failure():
    from core.services.rag import _expand_parents

    class _BoomDB:
        def query(self, *args, **kwargs):
            raise RuntimeError("db down")

    sources = [{"parent_id": "kb1-doc1-parent0", "snippet": "s", "content": "child 全文"}]
    _expand_parents(_BoomDB(), workspace_id=1, sources=sources)
    # 父块查询失败绝不拖垮主流程，content 保留 child 全文兜底
    assert sources[0]["content"] == "child 全文"


def test_knowledge_source_text_dedups_parent_and_prefers_content():
    from core.runtime.workflow import WorkflowRunner

    runner = WorkflowRunner.__new__(WorkflowRunner)
    sources = [
        {"title": "doc", "parent_id": "p0", "content": "父块0全文", "snippet": "child A"},
        {"title": "doc", "parent_id": "p0", "content": "父块0全文", "snippet": "child B"},
        {"title": "doc2", "parent_id": "p1", "content": "", "snippet": "无父块, 用 snippet"},
    ]
    text = runner._knowledge_source_text(sources)

    # 同父块只注入一次父块全文；无 content 的回退到 snippet
    assert text.count("父块0全文") == 1
    assert "无父块, 用 snippet" in text


def test_recover_interrupted_ingestion_resets_stuck_indexing():
    from core.services.knowledge import recover_interrupted_ingestion

    class _Doc:
        def __init__(self):
            self.status = "indexing"
            self.error_message = ""
            self.chunk_count = 7

    class _Query:
        def __init__(self, rows):
            self._rows = rows

        def filter(self, *a, **k):
            return self

        def all(self):
            return self._rows

    class _DB:
        def __init__(self, rows):
            self._rows = rows
            self.committed = False

        def query(self, *a, **k):
            return _Query(self._rows)

        def commit(self):
            self.committed = True

    docs = [_Doc(), _Doc()]
    db = _DB(docs)
    count = recover_interrupted_ingestion(db)

    assert count == 2
    assert db.committed is True
    for doc in docs:
        assert doc.status == "failed"
        assert "重启" in doc.error_message
        assert doc.chunk_count == 0

    # 无卡死文档时不应触发 commit
    empty_db = _DB([])
    assert recover_interrupted_ingestion(empty_db) == 0
    assert empty_db.committed is False


def test_chat_rag_options_emit_hybrid_status(client, auth_headers):
    kb = client.post("/api/knowledge-bases", headers=auth_headers, json={"name": "Hybrid KB"})
    kb_id = kb.json()["knowledge_base"]["id"]
    created_doc = client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        headers=auth_headers,
        json={"filename": "hybrid.txt", "text": "hybrid-source-token dense bm25 rrf rerank"},
    )
    assert created_doc.status_code == 200
    agent = client.post(
        "/api/agents",
        headers=auth_headers,
        json={
            "name": "Hybrid Agent",
            "knowledge_base_ids": [kb_id],
            "rag": {"enabled_by_default": True, "top_k": 1},
        },
    )
    agent_id = agent.json()["agent"]["id"]

    response = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={
            "message": "hybrid-source-token",
            "mode": "draft",
            "rag_enabled": True,
            "rag_options": {"top_k": 1, "dense_top_k": 4, "bm25_top_k": 4, "rerank_enabled": True},
        },
    )

    assert response.status_code == 200
    rag_status = _events(response.text, "rag_status")[-1]
    assert rag_status["dense"]["matched"] >= 1
    assert rag_status["bm25"]["matched"] >= 1
    assert rag_status["rrf"]["matched"] >= 1
    assert rag_status["rerank"]["enabled"] is True
    sources = _events(response.text, "sources")[-1]["items"]
    assert sources[0]["parent_id"]
    assert sources[0]["retrieval_channel"]


def test_knowledge_index_job_endpoint_has_redis_fallback(client, auth_headers):
    kb = client.post("/api/knowledge-bases", headers=auth_headers, json={"name": "Job KB"})
    kb_id = kb.json()["knowledge_base"]["id"]

    indexed = client.post(f"/api/knowledge-bases/{kb_id}/index", headers=auth_headers)
    assert indexed.status_code == 200
    job_id = indexed.json()["job_id"]

    job = client.get(f"/api/knowledge/jobs/{job_id}", headers=auth_headers)
    assert job.status_code == 200
    assert job.json()["job_id"] == job_id
    assert job.json()["status"] in {"succeeded", "unknown"}
    if job.json()["status"] == "succeeded":
        assert job.json()["documents_total"] == 0
        assert job.json()["chunks_indexed"] == 0


def test_bm25_search_batching(client, auth_headers, monkeypatch):
    # 模拟大量的 KnowledgeChunk 记录来验证分页检索逻辑
    from core.db.session import SessionLocal
    from core.db.models import Workspace, KnowledgeChunk
    from core.services.rag import _bm25_search
    
    db = SessionLocal()
    try:
        # 获取由 registration 自动创建的真实的 workspace
        workspace = db.query(Workspace).first()
        assert workspace is not None
        
        # 通过 API 创建一个真实的知识库以通过外键验证
        kb = client.post("/api/knowledge-bases", headers=auth_headers, json={"name": "Batch KB"})
        assert kb.status_code == 200
        kb_id = kb.json()["knowledge_base"]["id"]
        
        # 通过 API 创建一个真实的文档以通过外键验证并获取有效的 document_id
        doc_resp = client.post(
            f"/api/knowledge-bases/{kb_id}/documents",
            headers=auth_headers,
            json={"filename": "batch.txt", "text": "init batch text content"},
        )
        assert doc_resp.status_code == 200
        doc_id = doc_resp.json()["document"]["id"]
        
        # 写入 5 个模拟的分片，设置批大小小于该值即可验证循环分页
        chunks = []
        for i in range(5):
            chunk = KnowledgeChunk(
                workspace_id=workspace.id,
                knowledge_base_id=kb_id,
                document_id=doc_id,
                chunk_index=i,
                text=f"knowledge corpus batch data item number {i}",
                vector_id=f"vector-batch-{i}",
                chunk_id=f"chunk-batch-{i}",
                content_hash="mock_batch_test",
                embedding_model="test",
                embedding_dimension=4
            )
            db.add(chunk)
            chunks.append(chunk)
        db.commit()
        
        # 将检索时的批量设为 2（验证多轮分页获取）
        monkeypatch.setattr("core.services.rag.BM25_BATCH_SIZE", 2)
        
        # 执行 BM25 检索
        hits = _bm25_search(db, workspace_id=workspace.id, knowledge_base_ids=[kb_id], query="corpus data", limit=3)
        
        assert len(hits) == 3
        assert "knowledge corpus" in hits[0]["text"]
        
        # 清理测试数据
        for chunk in chunks:
            db.delete(chunk)
        db.commit()
    finally:
        db.close()


def test_has_evidence_logic():
    from core.services.rag import _has_evidence

    # 1. 没有任何召回片段时，应该算作没有证据
    assert _has_evidence([], "你好") is False

    # 2. 有召回片段，且有关键字精确匹配，应该算作有证据
    sources_keyword = [
        {
            "snippet": "这是一个关于作物 yield 预测的论文",
            "retrieval_channel": "rrf:dense",
            "dense_score": 0.20,
            "score": 0.02,
        }
    ]
    assert _has_evidence(sources_keyword, "yield") is True

    # 3. 有召回片段，无关键字直接重叠，但密集检索分数 >= 0.4，应该算作有证据
    sources_high_dense = [
        {
            "snippet": "作物预测模型采用 CNN-LSTM 网络",
            "retrieval_channel": "rrf:dense",
            "dense_score": 0.45,
            "score": 0.02,
        }
    ]
    assert _has_evidence(sources_high_dense, "作物能预测吗") is True

    # 4. 有召回片段，无关键字直接重叠，密集检索分数 < 0.4 (例如 0.21)，且没有任何关键字重叠（比如打招呼）
    # 这种情况在旧代码中会因为 rrf 分数 > 0 被错误识别为有证据，新代码应该正确识别为无证据
    sources_low_dense = [
        {
            "snippet": "作物预测模型采用 CNN-LSTM 网络",
            "retrieval_channel": "rrf:dense",
            "dense_score": 0.21,
            "score": 0.02,
        }
    ]
    assert _has_evidence(sources_low_dense, "你好，请问你是谁") is False

    # 5. 关键回归：BM25 单路命中但 query 全是停用词 —— 旧实现因 `bm25_score > 0` 恒真而误判为有证据。
    #    典型场景：用户问「你好呀」，jieba 切出「你好/呀」全被停用词表拦掉，
    #    但 BM25 仍因字符交叠给出正分，于是知识库被强制注入，诱发幻觉。
    sources_bm25_stopword_only = [
        {
            "snippet": "你好，这是产品使用说明的第一段。",
            "retrieval_channel": "rrf:dense+bm25",
            "dense_score": 0.12,
            "bm25_score": 3.7,
            "score": 0.016,
        }
    ]
    assert _has_evidence(sources_bm25_stopword_only, "你好呀") is False

    # 6. 仅凭 BM25 微弱正分（无 rerank、dense 低于阈值）不足以构成证据。
    sources_bm25_weak = [
        {
            "snippet": "系统采用向量检索与词频检索双通道融合。",
            "retrieval_channel": "rrf:dense+bm25",
            "dense_score": 0.18,
            "bm25_score": 1.02,
            "score": 0.016,
        }
    ]
    assert _has_evidence(sources_bm25_weak, "量子隧穿效应如何解释") is False

