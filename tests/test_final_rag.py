import json

import pytest

from api.schemas import AgentCreateRequest, AgentRagConfig
from core.config import get_settings
from core.db.models import Agent
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


def test_normalize_rag_empty_config_keeps_settings_defaults():
    """空配置（键全缺）必须落到 settings 默认值，而不是被 None 归零成 False。"""
    settings = get_settings()
    config = normalize_rag(AgentRagConfig().model_dump())

    assert config["rerank_enabled"] == settings.rag_rerank_enabled
    assert config["cache_enabled"] == settings.rag_cache_enabled
    assert config["refuse_when_no_evidence"] == settings.rag_refuse_when_no_evidence


def test_normalize_rag_frontend_create_payload_keeps_refusal_on():
    """前端建 agent 的真实载荷形状：pydantic 会把未提供的 bool 字段补成 None，拒答必须仍为开启。"""
    payload = AgentCreateRequest(name="t").model_dump()["rag"]
    config = normalize_rag(payload)

    assert config["rerank_enabled"] is True
    assert config["cache_enabled"] is True
    assert config["refuse_when_no_evidence"] is True


def test_normalize_rag_explicit_false_is_respected():
    """显式 opt-out 必须保住：三个 bool 字段传 False 时不得回落默认值。"""
    config = normalize_rag({"rerank_enabled": False, "cache_enabled": False, "refuse_when_no_evidence": False})

    assert config["rerank_enabled"] is False
    assert config["cache_enabled"] is False
    assert config["refuse_when_no_evidence"] is False


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


def test_retrieve_judges_evidence_on_original_query_not_bm25_translation(client, auth_headers, monkeypatch):
    """真实环境回归：跨语言翻译只为 BM25 检索英文文档服务。证据判定若拿翻译后的英文去和中文
    片段比词面，「生日有什么优惠」这类换说法问题的词面证据永远命中不到，就会被误拒。"""
    from core.db.session import SessionLocal
    from core.services import rag

    kb_id = client.post("/api/knowledge-bases", headers=auth_headers, json={"name": "Translate KB"}).json()["knowledge_base"]["id"]
    added = client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        headers=auth_headers,
        json={"title": "会员手册", "text": "黄金会员每月可以领取 2 张免费饮品券，生日当天全单 7 折。"},
    )
    assert added.status_code == 200
    monkeypatch.setattr(rag, "translate_query", lambda provider, query, **kwargs: "What birthday discounts are there?")

    db = SessionLocal()
    try:
        workspace_id = db.query(Agent.workspace_id).first()[0]
        result = rag.retrieve(
            db, workspace_id=workspace_id, knowledge_base_ids=[kb_id], query="生日有什么优惠",
            config={"cache_enabled": False, "rerank_enabled": False},
        )
    finally:
        db.close()

    assert result.sources
    assert result.status["translate"]["applied"] is True
    assert result.status["no_evidence"] is False


def test_all_disabled_knowledge_bases_refuse_without_rewriting_ids(client, auth_headers):
    """全部知识库停用：rag.retrieve() 如实报 all_knowledge_bases_disabled，且端到端拒答。

    core/services/rag.py 的 all_knowledge_bases_disabled 支路此前零覆盖。该支路的
    正确性依赖两件事同时成立：
      1. reason/no_evidence 如实反映「有 KB 绑定但一个都没启用」；
      2. knowledge_base_ids **保持原始非空值**——workflow 的拒答条件是
         `status.get("no_evidence") and status.get("knowledge_base_ids")`，
         若这里把 ids 改写成空（或直接复用剔除后的 enabled_ids），
         停用 KB 的 agent 就会像「没配 KB」一样静默放行，拒答契约失效。

    断言分两层，各查各的东西，不混：
      - retrieve() 原始返回：查 rag.py 支路本身（reason/no_evidence/ids 未被改写）。
        不能只查端到端事件流——workflow 会在拒答时把 reason 覆盖成
        no_evidence_refused，那层看不到 all_knowledge_bases_disabled 这个原值。
      - 端到端：查该 status 确实触发了拒答（用户侧可见），因为第二层依赖的
        knowledge_base_ids 非空只有走完 workflow 才被真正消费。

    注：本测试在创建 agent 时显式传 `refuse_when_no_evidence: True`，与
    test_platform_api.py::test_rag_no_evidence_refuses_instead_of_guessing 的既有写法一致。

    历史背景（避免误读）：该字段在 api/schemas.py 的 AgentRagConfig 里声明为
    `bool | None = None`，而 normalize_rag 早先用 `data.get(k, settings.rag_xxx)` —— 键存在
    但值为 None 时默认值不生效，bool(None) 得 False。于是「不显式传该字段」建出的 agent
    拒答默认是关的。该缺陷已由 3eccc59 修复（引入 _bool_or：None 视为未提供、取 settings 默认；
    显式 False 仍被尊重）。**故现在不显式传该字段也会正确取到 True**；
    此处保留显式传值仅为了让测试意图自明，不再是必需。
    """
    from core.db.session import SessionLocal
    from core.services.rag import retrieve

    kb_id = client.post("/api/knowledge-bases", headers=auth_headers, json={"name": "Disabled KB"}).json()[
        "knowledge_base"
    ]["id"]
    patched = client.patch(f"/api/knowledge-bases/{kb_id}", headers=auth_headers, json={"enabled": False})
    assert patched.status_code == 200
    assert patched.json()["knowledge_base"]["enabled"] is False

    agent_id = client.post(
        "/api/agents",
        headers=auth_headers,
        json={
            "name": "Disabled KB Agent",
            "knowledge_base_ids": [kb_id],
            "rag": {"enabled_by_default": True, "refuse_when_no_evidence": True},
        },
    ).json()["agent"]["id"]

    # 第 1 层：rag.py 支路原始输出（未被 workflow 覆写过的 reason）。
    db = SessionLocal()
    try:
        agent = db.get(Agent, agent_id)
        result = retrieve(
            db,
            workspace_id=agent.workspace_id,
            knowledge_base_ids=[kb_id],
            query="hello there",
            config={},
        )
    finally:
        db.close()

    assert result.sources == []
    assert result.status["reason"] == "all_knowledge_bases_disabled"
    assert result.status["no_evidence"] is True
    # 护栏：原始 knowledge_base_ids 必须原样保留（拒答条件依赖它非空）。
    assert result.status["knowledge_base_ids"] == [kb_id]

    # 第 2 层：端到端确实拒答（而非静默放行给模型）。
    response = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "hello there", "mode": "draft", "rag_enabled": True},
    )

    assert response.status_code == 200
    rag_status = _events(response.text, "rag_status")[-1]
    assert rag_status["no_evidence"] is True
    assert rag_status["knowledge_base_ids"] == [kb_id]
    assert '"rag_refusal": true' in response.text
    assert "No reliable evidence was found" in response.text


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


def test_has_evidence_ignores_dense_when_rerank_applied():
    """真实环境回归（bge-m3 + bge-reranker-v2-m3）：无关问题 dense 常在 0.43~0.53，
    越过 0.40 就被判有证据；rerank 对它们 ≤0.02。rerank 生效时 dense 不能单独判有证据。"""
    from core.services.rag import _has_evidence

    unrelated = [
        {"snippet": "招牌饮品叫雾岛拿铁，售价 36 元。", "retrieval_channel": "rerank", "dense_score": 0.529, "score": 0.018},
        {"snippet": "会员分为青铜、白银、黄金三级。", "retrieval_channel": "rerank", "dense_score": 0.515, "score": 0.0},
    ]
    assert _has_evidence(unrelated, "星巴克的大杯美式多少钱？", rerank_applied=True) is False

    related = [{"snippet": "营业时间为上午 9 点 30 分到晚上 8 点 45 分。", "retrieval_channel": "rerank", "dense_score": 0.30, "score": 0.998}]
    assert _has_evidence(related, "几点营业", rerank_applied=True) is True


def test_has_evidence_keeps_lexical_match_when_rerank_score_is_low():
    """rerank 也不能单独当裁判：换说法的正常问题（实测「生日有什么优惠」rerank 仅 0.11）
    同样低于阈值，只看 rerank 会大面积误拒；词面命中必须保留。"""
    from core.services.rag import _has_evidence

    sources = [{"snippet": "黄金会员每月可以领取 2 张免费饮品券，生日当天全单 7 折。", "retrieval_channel": "rerank", "dense_score": 0.50, "score": 0.114}]
    assert _has_evidence(sources, "生日有什么优惠", rerank_applied=True) is True


def test_has_evidence_falls_back_to_lexical_and_dense_without_rerank():
    """rerank 未开启或调用失败时，退回词面 + dense 双档（行为与此前一致）。"""
    from core.services.rag import _has_evidence

    sources = [{"snippet": "作物预测模型采用 CNN-LSTM 网络", "retrieval_channel": "rrf:dense", "dense_score": 0.45, "score": 0.02}]
    assert _has_evidence(sources, "作物能预测吗", rerank_applied=False) is True


@pytest.mark.parametrize("text", ["你好", "您好！", "hi", "Hello~", "谢谢", "谢谢你", "好的，谢谢", "再见", "早上好呀", "在吗？", "嗨 😊"])
def test_small_talk_detects_pure_greetings(text):
    from core.services.rag import is_small_talk

    assert is_small_talk(text) is True


@pytest.mark.parametrize("text", ["你好，几点开门", "今天天气怎么样", "法国的首都是哪里", "谢谢，会员怎么升级", "hello world 是什么", ""])
def test_small_talk_rejects_real_questions(text):
    from core.services.rag import is_small_talk

    assert is_small_talk(text) is False

