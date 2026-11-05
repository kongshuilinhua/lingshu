"""独立工作流：创建 → 引用 → 试运行 一条链路。

只覆盖核心路径：建一个工作流、让智能体引用它、拿它跑一次。
节点级细节由 `test_flow_nodes.py` 和 `test_workflow_graph.py` 覆盖，这里不重复。
"""


def _create_workflow(client, headers, name="测试工作流"):
    response = client.post("/api/workflows", headers=headers, json={"name": name})
    assert response.status_code == 200
    return response.json()


def test_workflow_crud(client, auth_headers):
    created = _create_workflow(client, auth_headers)
    workflow_id = created["workflow"]["id"]
    # 新建即带一份可用的默认流程，不要求用户从空画布开始
    assert [node["type"] for node in created["nodes"]] == ["Start", "Knowledge", "Tool", "LLM", "Answer"]

    listed = client.get("/api/workflows", headers=auth_headers)
    assert [item["id"] for item in listed.json()["items"]] == [workflow_id]

    updated = client.patch(
        f"/api/workflows/{workflow_id}",
        headers=auth_headers,
        json={"name": "改名后的工作流", "description": "说明"},
    )
    assert updated.status_code == 200
    assert updated.json()["workflow"]["name"] == "改名后的工作流"

    deleted = client.delete(f"/api/workflows/{workflow_id}", headers=auth_headers)
    assert deleted.status_code == 200
    assert client.get("/api/workflows", headers=auth_headers).json()["items"] == []


def test_workflow_rejects_invalid_graph(client, auth_headers):
    created = _create_workflow(client, auth_headers, name="坏图")
    workflow_id = created["workflow"]["id"]
    response = client.patch(
        f"/api/workflows/{workflow_id}",
        headers=auth_headers,
        json={"nodes": [{"id": "start", "type": "Start", "name": "开始", "config": {}}]},
    )
    assert response.status_code == 400
    assert "Answer" in response.json()["detail"]


def test_agent_references_workflow_then_test_runs(client, auth_headers):
    created_workflow = _create_workflow(client, auth_headers, name="两节点流程")
    workflow_id = created_workflow["workflow"]["id"]

    agent = client.post(
        "/api/agents",
        headers=auth_headers,
        json={"name": "引用工作流的智能体", "system_prompt": "你是测试助手。"},
    ).json()["agent"]

    linked = client.patch(
        f"/api/agents/{agent['id']}",
        headers=auth_headers,
        json={"workflow_id": workflow_id},
    )
    assert linked.status_code == 200
    assert linked.json()["agent"]["workflow_id"] == workflow_id

    # 智能体详情展示的流程应当来自被引用的工作流，而不是内置默认流程
    detail = client.get(f"/api/agents/{agent['id']}", headers=auth_headers).json()["agent"]
    assert detail["workflow_id"] == workflow_id
    assert [node["id"] for node in detail["workflow"]] == [node["id"] for node in created_workflow["nodes"]]

    tested = client.post(
        f"/api/workflows/{workflow_id}/test",
        headers=auth_headers,
        json={"agent_id": agent["id"], "message": "你好"},
    )
    assert tested.status_code == 200
    payload = tested.json()
    assert payload["status"] == "succeeded"
    assert [step["node_type"] for step in payload["steps"]] == ["Start", "Knowledge", "Tool", "LLM", "Answer"]

    # 删除被引用的工作流：智能体退回内置流程，不留悬空引用
    assert client.delete(f"/api/workflows/{workflow_id}", headers=auth_headers).status_code == 200
    after = client.get(f"/api/agents/{agent['id']}", headers=auth_headers).json()["agent"]
    assert after["workflow_id"] is None


def test_workflow_test_requires_existing_agent(client, auth_headers):
    created = _create_workflow(client, auth_headers, name="无人可跑")
    response = client.post(
        f"/api/workflows/{created['workflow']['id']}/test",
        headers=auth_headers,
        json={"agent_id": 99999, "message": "你好"},
    )
    assert response.status_code == 404


def test_agent_node_feeds_answer_directly(client, auth_headers):
    """Start → 智能体 → 输出回答：不垫 LLM 节点也能拿到子智能体的回答。"""
    worker = client.post(
        "/api/agents",
        headers=auth_headers,
        json={"name": "干活的智能体", "system_prompt": "你负责回答。"},
    ).json()["agent"]
    assert client.post(f"/api/agents/{worker['id']}/publish", headers=auth_headers).status_code == 200

    created = _create_workflow(client, auth_headers, name="只调智能体")
    workflow_id = created["workflow"]["id"]
    nodes = [
        {"id": "start", "type": "Start", "name": "开始", "config": {}},
        {"id": "worker", "type": "Agent", "name": "调用智能体", "config": {"agent_id": worker["id"], "input": "{{input}}"}},
        {"id": "answer", "type": "Answer", "name": "输出回答", "config": {}},
    ]
    saved = client.patch(f"/api/workflows/{workflow_id}", headers=auth_headers, json={"nodes": nodes})
    assert saved.status_code == 200

    host = client.post("/api/agents", headers=auth_headers, json={"name": "试运行宿主"}).json()["agent"]
    tested = client.post(
        f"/api/workflows/{workflow_id}/test",
        headers=auth_headers,
        json={"agent_id": host["id"], "message": "你好"},
    )
    assert tested.status_code == 200, tested.text
    payload = tested.json()
    assert payload["status"] == "succeeded"
    assert [step["node_type"] for step in payload["steps"]] == ["Start", "Agent", "Answer"]
    assert payload["answer"]


def test_workflow_chats_directly_with_its_own_runtime(client, auth_headers):
    """工作流自带运行配置：设好模型后可以直接对话，不必挂在某个智能体上。"""
    created = _create_workflow(client, auth_headers, name="直连对话")
    workflow_id = created["workflow"]["id"]
    # 新建即自动配好宿主智能体（承接会话与运行）
    assert created["workflow"]["agent_id"]

    listed = client.get("/api/workflows", headers=auth_headers).json()["items"]
    assert listed[0]["agent_id"] == created["workflow"]["agent_id"]

    # 宿主智能体会被标出来（前端据此从智能体列表与市场里隐藏）
    agents = {item["id"]: item for item in client.get("/api/agents", headers=auth_headers).json()["items"]}
    host = agents[created["workflow"]["agent_id"]]
    assert host["is_workflow_host"] is True

    response = client.post(
        f"/api/workflows/{workflow_id}/chat/stream",
        headers=auth_headers,
        json={"message": "你好", "mode": "published"},
    )
    assert response.status_code == 200, response.text
    assert "event: done" in response.text


def test_parallel_iteration_runs_end_to_end(client, auth_headers):
    """并行迭代走真实执行器：每个线程各开数据库会话，结果按原顺序汇总给下游。"""
    created = _create_workflow(client, auth_headers, name="并行迭代")
    workflow_id = created["workflow"]["id"]
    nodes = [
        {"id": "start", "type": "Start", "name": "开始", "config": {}},
        {"id": "split", "type": "VariableAssign", "name": "拆分", "config": {"assignments": [
            {"name": "topics", "value": ["甲", "乙", "丙", "丁"]},
        ]}},
        {"id": "each", "type": "Loop", "name": "逐个处理", "config": {
            "items": "{{variables.topics}}", "parallel": 3, "on_error": "skip",
            "body": [{"id": "llm_in_loop", "type": "LLM", "name": "处理", "config": {}}],
        }},
        {"id": "llm", "type": "LLM", "name": "汇总", "config": {}},
        {"id": "answer", "type": "Answer", "name": "输出", "config": {}},
    ]
    assert client.patch(f"/api/workflows/{workflow_id}", headers=auth_headers, json={"nodes": nodes}).status_code == 200
    host = client.post("/api/agents", headers=auth_headers, json={"name": "试运行宿主"}).json()["agent"]
    tested = client.post(f"/api/workflows/{workflow_id}/test", headers=auth_headers, json={"agent_id": host["id"], "message": "逐个处理"})
    assert tested.status_code == 200, tested.text
    assert tested.json()["status"] == "succeeded"
    assert [step["node_type"] for step in tested.json()["steps"]] == ["Start", "VariableAssign", "Loop", "LLM", "Answer"]


def test_variable_aggregate_merges_condition_branches(client, auth_headers):
    """条件分两路各自赋值，聚合后下游只认一个变量；跑哪条分支都能拿到值。"""
    created = _create_workflow(client, auth_headers, name="分支汇合")
    workflow_id = created["workflow"]["id"]
    nodes = [
        {"id": "start", "type": "Start", "name": "开始", "config": {}, "next": "route"},
        {"id": "route", "type": "Condition", "name": "分流", "config": {
            "branches": [{"when": {"field": "input", "eq": "我要退货"}, "next": "a"}], "default": "b",
        }},
        {"id": "a", "type": "VariableAssign", "name": "退货线", "config": {"assignments": [{"name": "ra", "value": "走退货流程"}]}, "next": "merge"},
        {"id": "b", "type": "VariableAssign", "name": "其他线", "config": {"assignments": [{"name": "rb", "value": "走通用流程"}]}, "next": "merge"},
        {"id": "merge", "type": "VariableAggregate", "name": "汇合", "config": {
            "sources": ["{{variables.ra}}", "{{variables.rb}}"], "output_var": "route_result",
        }},
        {"id": "llm", "type": "LLM", "name": "回答", "config": {}},
        {"id": "answer", "type": "Answer", "name": "输出", "config": {}},
    ]
    saved = client.patch(f"/api/workflows/{workflow_id}", headers=auth_headers, json={"nodes": nodes})
    assert saved.status_code == 200, saved.text
    host = client.post("/api/agents", headers=auth_headers, json={"name": "试运行宿主"}).json()["agent"]
    for message, branch in (("我要退货", "VariableAssign"), ("你好", "VariableAssign")):
        tested = client.post(f"/api/workflows/{workflow_id}/test", headers=auth_headers, json={"agent_id": host["id"], "message": message})
        assert tested.status_code == 200, tested.text
        types = [step["node_type"] for step in tested.json()["steps"]]
        assert types == ["Start", "Condition", branch, "VariableAggregate", "LLM", "Answer"]


def test_llm_node_prompt_template_reaches_model(client, auth_headers, monkeypatch):
    """LLM 节点自带提示词时，发给模型的用户消息是渲染后的提示词，而不是用户原话。"""
    from core.integrations import llm as llm_module

    seen = []
    original = llm_module.OpenAICompatibleProvider.chat

    def spy(self, messages, *args, **kwargs):
        seen.append(messages[-1]["content"])
        return original(self, messages, *args, **kwargs)

    monkeypatch.setattr(llm_module.OpenAICompatibleProvider, "chat", spy)
    created = _create_workflow(client, auth_headers, name="逐项提示词")
    nodes = [
        {"id": "start", "type": "Start", "name": "开始", "config": {}},
        {"id": "set", "type": "VariableAssign", "name": "列表", "config": {"assignments": [{"name": "words", "value": ["甲", "乙"]}]}},
        {"id": "each", "type": "Loop", "name": "逐项", "config": {
            "items": "{{variables.words}}", "loop_var": "w",
            "body": [{"id": "tr", "type": "LLM", "name": "处理", "config": {"prompt": "处理「{{variables.w}}」"}}],
        }},
        {"id": "llm", "type": "LLM", "name": "汇总", "config": {}},
        {"id": "answer", "type": "Answer", "name": "输出", "config": {}},
    ]
    assert client.patch(f"/api/workflows/{created['workflow']['id']}", headers=auth_headers, json={"nodes": nodes}).status_code == 200
    host = client.post("/api/agents", headers=auth_headers, json={"name": "宿主"}).json()["agent"]
    tested = client.post(f"/api/workflows/{created['workflow']['id']}/test", headers=auth_headers, json={"agent_id": host["id"], "message": "开始"})
    assert tested.status_code == 200, tested.text
    assert "处理「甲」" in seen and "处理「乙」" in seen
