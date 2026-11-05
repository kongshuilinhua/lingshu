"""元数据过滤：按文档元数据圈定检索范围，过滤要下推到向量与 BM25 两路。"""


def _kb(client, headers):
    return client.post("/api/knowledge-bases", headers=headers, json={"name": "元数据库"}).json()["knowledge_base"]


def _doc(client, headers, kb, filename, text, metadata):
    doc = client.post(
        f"/api/knowledge-bases/{kb['id']}/documents", headers=headers, json={"filename": filename, "text": text}
    ).json()["document"]
    response = client.put(
        f"/api/knowledge-bases/{kb['id']}/documents/{doc['id']}/metadata", headers=headers, json={"metadata": metadata}
    )
    assert response.status_code == 200, response.text
    assert response.json()["document"]["metadata"] == metadata
    return doc


def _test(client, headers, kb, query, metadata_filter=None):
    body = {"query": query, "rerank_enabled": False}
    if metadata_filter:
        body["metadata_filter"] = metadata_filter
    return client.post(f"/api/knowledge-bases/{kb['id']}/retrieval-test", headers=headers, json=body).json()


def test_metadata_filter_scopes_retrieval(client, auth_headers):
    kb = _kb(client, auth_headers)
    a = _doc(client, auth_headers, kb, "a.txt", "保修期为一年，人为损坏不在保修范围。", {"产品": "扫地机"})
    b = _doc(client, auth_headers, kb, "b.txt", "保修期为两年，电池单独保修半年。", {"产品": "洗地机"})

    unfiltered = {item["document_id"] for item in _test(client, auth_headers, kb, "保修期多久")["items"]}
    assert {a["id"], b["id"]} <= unfiltered

    only_b = _test(client, auth_headers, kb, "保修期多久", {"logic": "and", "conditions": [{"key": "产品", "op": "eq", "value": "洗地机"}]})
    assert {item["document_id"] for item in only_b["items"]} == {b["id"]}
    assert only_b["status"]["metadata_filter"]["documents"] == 1

    either = _test(client, auth_headers, kb, "保修期多久", {"logic": "or", "conditions": [
        {"key": "产品", "op": "eq", "value": "扫地机"}, {"key": "产品", "op": "contains", "value": "洗地"},
    ]})
    assert {item["document_id"] for item in either["items"]} == {a["id"], b["id"]}


def test_metadata_filter_with_no_document_returns_nothing(client, auth_headers):
    kb = _kb(client, auth_headers)
    _doc(client, auth_headers, kb, "a.txt", "保修期为一年。", {"产品": "扫地机"})
    body = _test(client, auth_headers, kb, "保修期", {"conditions": [{"key": "产品", "op": "eq", "value": "不存在"}]})
    assert body["items"] == []
    assert body["status"]["reason"] == "metadata_filter_no_document"


def test_knowledge_node_filter_reads_variables(client, auth_headers):
    """工作流里条件值可以引用变量：先抽出产品名，再只在该产品的文档里检索。"""
    from core.db.session import SessionLocal
    from core.runtime.workflow import WorkflowRunner
    from core.db.models import Agent, Session as ChatSession

    kb = _kb(client, auth_headers)
    _doc(client, auth_headers, kb, "a.txt", "扫地机保修一年。", {"产品": "扫地机"})
    target = _doc(client, auth_headers, kb, "b.txt", "洗地机保修两年。", {"产品": "洗地机"})
    agent = client.post(
        "/api/agents", headers=auth_headers, json={"name": "售后", "knowledge_base_ids": [kb["id"]], "rag": {"retrieval_mode": "auto", "rerank_enabled": False}}
    ).json()["agent"]
    nodes = [
        {"id": "start", "type": "Start", "name": "开始", "config": {}},
        {"id": "kb", "type": "Knowledge", "name": "检索", "config": {
            "metadata_filter": {"conditions": [{"key": "产品", "op": "eq", "value": "{{variables.product}}"}]},
        }},
        {"id": "llm", "type": "LLM", "name": "回答", "config": {}},
        {"id": "answer", "type": "Answer", "name": "输出", "config": {}},
    ]
    with SessionLocal() as db:
        row = db.get(Agent, agent["id"])
        session = ChatSession(workspace_id=row.workspace_id, agent_id=row.id, user_id=row.created_by, title="t", is_debug=True)
        db.add(session)
        db.commit()
        _run, _answer, sources, _steps = WorkflowRunner(db).run(
            agent=row, chat_session=session, user_message="保修多久", mode="draft",
            variables={"product": "洗地机"}, workflow_override=nodes,
        )
    assert sources
    assert {item.get("document_id") for item in sources} == {target["id"]}
