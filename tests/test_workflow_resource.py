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
