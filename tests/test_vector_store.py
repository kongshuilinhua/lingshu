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
    """检索在降级态可继续（读内存），只有写入被拒。

    判别力要求：本用例必须证明读路径真的走到了内存后端。只断言 hits == []
    是没有判别力的 —— 把 MilvusVectorStore.search 改成直接 `return []`
    （根本不查内存）同样通过，等于一条假装在测东西的测试。
    故这里先往降级保底实例写入一条向量，再断言 search 能把它检索出来。
    """
    from core.integrations import vector_store as vs_module

    store = vs_module.MilvusVectorStore()
    monkeypatch.setattr(store.settings, "vector_backend", "milvus", raising=False)
    # 模拟连接失败后的降级态
    store._client = None
    store._error = "connection refused"

    # 降级态 upsert 会抛（见上一条用例），所以直接写保底实例 _fallback。
    # 它由 __init__ 无条件构造（MilvusVectorStore.__init__ 里 self._fallback = MemoryVectorStore()），
    # 不依赖任何连接，构造时机普通，可稳定写入。
    store._fallback.upsert("vec-fallback-1", [0.1, 0.2, 0.3], "fallback-text", {"workspace_id": 1})

    hits = store.search([0.1, 0.2, 0.3], limit=5, filters={"workspace_id": 1})

    assert [hit.vector_id for hit in hits] == ["vec-fallback-1"]
    assert hits[0].text == "fallback-text"
    # 过滤仍在内存后端生效：其他 workspace 不得被召回。
    assert store.search([0.1, 0.2, 0.3], limit=5, filters={"workspace_id": 2}) == []

