"""Human-in-the-loop 测试:HumanApproval 节点的 pause 机制(方向 4 切片 1)。

覆盖:
1. validate_graph 接受 HumanApproval 节点。
2. _execute_node 对 HumanApproval 返回 paused output。
3. run_events 真实游标循环跑到 HumanApproval → Run 置 paused + 发 paused 事件 + 提前结束(后续节点不执行)。
"""

from types import SimpleNamespace

from core.runtime.graph import validate_graph
from core.runtime.workflow import WorkflowRunner


def test_validate_graph_accepts_human_approval():
    nodes = [
        {"id": "start", "type": "Start", "next": "approval"},
        {"id": "approval", "type": "HumanApproval", "config": {"prompt": "确认?"}, "next": "answer"},
        {"id": "answer", "type": "Answer"},
    ]
    assert validate_graph(nodes) == []


def test_execute_node_human_approval_returns_paused():
    runner = WorkflowRunner(db=None)
    output = runner._execute_node(
        SimpleNamespace(),
        {"id": "approval", "type": "HumanApproval", "config": {"prompt": "确认继续?"}},
        {},
    )
    assert output["paused"] is True
    assert output["approval_prompt"] == "确认继续?"


def test_run_events_human_approval_pauses_before_answer(monkeypatch):
    """跑到 HumanApproval 触发 pause,Answer 不执行,Run 置 paused,发 paused 事件。"""
    workflow = [
        {"id": "start", "type": "Start", "next": "approval"},
        {"id": "approval", "type": "HumanApproval", "config": {"prompt": "确认继续?"}, "next": "answer"},
        {"id": "answer", "type": "Answer"},
    ]
    executed: list[str] = []
    captured: dict = {}

    def fake_execute(self, agent, node, context):
        executed.append(node["id"])
        if node["type"] == "HumanApproval":
            return {"paused": True, "approval_prompt": node.get("config", {}).get("prompt")}
        if node["type"] == "Answer":
            return {"answer": "答案"}
        return {}

    monkeypatch.setattr(WorkflowRunner, "_execute_node", fake_execute)

    def fake_start_run(self, **kwargs):
        runtime = SimpleNamespace(workflow=workflow, settings={"memory": {"enabled": False}})
        run = SimpleNamespace(id=1, status="running")
        captured["run"] = run
        context = {"rag_enabled": False, "draft": "", "sources": [], "web_sources": [], "tool_outputs": []}
        return runtime, run, context

    monkeypatch.setattr(WorkflowRunner, "_start_run", fake_start_run)

    def fake_persist(self, run, node, user_message, output, *, status="succeeded"):
        return SimpleNamespace(id=id(node), node_id=node["id"], node_type=node["type"], status=status)

    monkeypatch.setattr(WorkflowRunner, "_persist_step", fake_persist)

    runner = WorkflowRunner(db=SimpleNamespace(commit=lambda: None, flush=lambda: None, refresh=lambda obj: None))
    events = list(
        runner.run_events(
            agent=SimpleNamespace(),
            chat_session=SimpleNamespace(id=1, user_id=1),
            user_message="hi",
            mode="draft",
        )
    )
    # HumanApproval 触发 pause,Answer 不执行
    assert executed == ["start", "approval"]
    # Run 被置 paused
    assert captured["run"].status == "paused"
    # 发了 paused 事件,且没有 complete 事件(提前 return)
    assert any(e.get("event") == "paused" for e in events)
    assert not any(e.get("event") == "complete" for e in events)
    # paused 事件携带 node_id 和 prompt
    paused = next(e for e in events if e.get("event") == "paused")
    assert paused["data"]["node_id"] == "approval"
    assert paused["data"]["prompt"] == "确认继续?"


def test_run_events_without_approval_runs_to_completion(monkeypatch):
    """回归:无 HumanApproval 的线性 workflow 不受 pause 检测影响,正常跑完。"""
    workflow = [
        {"id": "start", "type": "Start"},
        {"id": "answer", "type": "Answer"},
    ]
    executed: list[str] = []
    captured: dict = {}

    def fake_execute(self, agent, node, context):
        executed.append(node["id"])
        if node["type"] == "Answer":
            return {"answer": "答案"}
        return {}

    monkeypatch.setattr(WorkflowRunner, "_execute_node", fake_execute)

    def fake_start_run(self, **kwargs):
        run = SimpleNamespace(id=1, status="running")
        captured["run"] = run
        return SimpleNamespace(workflow=workflow, settings={"memory": {"enabled": False}}), run, {"draft": "", "sources": [], "web_sources": [], "tool_outputs": []}

    monkeypatch.setattr(WorkflowRunner, "_start_run", fake_start_run)
    monkeypatch.setattr(WorkflowRunner, "_persist_step", lambda self, run, node, um, output, *, status="succeeded": SimpleNamespace(id=id(node), node_id=node["id"], node_type=node["type"], status=status))

    runner = WorkflowRunner(db=SimpleNamespace(commit=lambda: None, flush=lambda: None, refresh=lambda obj: None))
    events = list(runner.run_events(agent=SimpleNamespace(), chat_session=SimpleNamespace(id=1, user_id=1), user_message="hi", mode="draft"))
    assert executed == ["start", "answer"]
    assert captured["run"].status == "succeeded"
    assert any(e.get("event") == "complete" for e in events)
