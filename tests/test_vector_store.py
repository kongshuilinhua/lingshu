import pytest

from core.integrations.vector_store import build_milvus_filter


def test_build_milvus_filter():
    expr = build_milvus_filter({"workspace_id": 1, "knowledge_base_id": 2, "document_id": "doc-1"})

    assert "workspace_id == 1" in expr
    assert "knowledge_base_id == 2" in expr
    assert 'document_id == "doc-1"' in expr


def test_upsert_refuses_to_write_when_fallback(monkeypatch):
    """降级态必须拒绝写入，而不是静默写内存。

    旧行为：Milvus 不可用时 upsert 把向量塞进进程内 MemoryVectorStore，
    进程重启即丢，而 MySQL 的 KnowledgeChunk 记录仍在 —— DB 与向量库永久不一致且无人察觉。
    """
    from core.integrations import vector_store as vs_module

    store = vs_module.MilvusVectorStore()
    monkeypatch.setattr(store.settings, "vector_backend", "milvus", raising=False)
    # 模拟连接失败后的降级态
    store._client = None
    store._error = "connection refused"

    assert store.using_fallback is True

    with pytest.raises(vs_module.VectorStoreUnavailableError):
        store.upsert("vec-1", [0.1, 0.2, 0.3], "text", {"workspace_id": 1})


def test_search_still_allowed_when_fallback(monkeypatch):
    """检索在降级态可继续（读内存），只有写入被拒。"""
    from core.integrations import vector_store as vs_module

    store = vs_module.MilvusVectorStore()
    monkeypatch.setattr(store.settings, "vector_backend", "milvus", raising=False)
    store._client = None
    store._error = "connection refused"

    hits = store.search([0.1, 0.2, 0.3], limit=5, filters={"workspace_id": 1})
    assert hits == []

