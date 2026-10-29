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


# ── 恢复（resume）────────────────────────────────────────────


def _runner_with_paused_run(workflow, *, executed=None, next_index=0, context=None):
    """构造一个已暂停的 Run，以及能跑 _advance_workflow 的 runner。"""
    run = SimpleNamespace(id=7, status="paused", resume_state={
        "mode": "draft",
        "workflow": workflow,
        "context": context or {"input": "hi", "sources": [], "draft": "", "tool_outputs": []},
        "upload_ids": [],
        "next_index": next_index,
        "executed": executed or ["start", "approval"],
        "paused_node_id": "approval",
    }, completed_at=None)
    db = SimpleNamespace(commit=lambda: None, flush=lambda: None, refresh=lambda obj: None)
    runner = WorkflowRunner(db=db)
    return runner, run


def test_resume_continues_after_approval_node(monkeypatch):
    """放行后从审批节点的下一跳继续，不重新执行审批节点本身。"""
    workflow = [
        {"id": "start", "type": "Start", "next": "approval"},
        {"id": "approval", "type": "HumanApproval", "config": {"prompt": "确认?"}, "next": "answer"},
        {"id": "answer", "type": "Answer"},
    ]
    executed: list[str] = []

    def fake_execute(self, agent, node, context):
        executed.append(node["id"])
        if node["type"] == "Answer":
            return {"answer": "继续后的答案"}
        return {}

    monkeypatch.setattr(WorkflowRunner, "_execute_node", fake_execute)
    monkeypatch.setattr(WorkflowRunner, "_persist_step",
                        lambda self, run, node, um, output, *, status="succeeded": SimpleNamespace(
                            id=id(node), node_id=node["id"], node_type=node["type"], status=status))
    monkeypatch.setattr(WorkflowRunner, "_runtime_agent",
                        lambda self, agent, mode, user_id, model_override=None: SimpleNamespace(
                            workflow=workflow, settings={"memory": {"enabled": False}}, runtime_config=None))

    runner, run = _runner_with_paused_run(workflow, next_index=2)
    events = list(runner.resume_events(
        run=run, agent=SimpleNamespace(id=1, workspace_id=1),
        chat_session=SimpleNamespace(id=1, user_id=1), user_message="hi", approved=True,
    ))

    assert executed == ["answer"]
    assert run.status == "succeeded"
    complete = next(e for e in events if e["event"] == "complete")
    assert complete["answer"] == "继续后的答案"


def test_resume_rejected_skips_remaining_nodes():
    """驳回时不执行任何后续节点，Run 置 rejected 并清空恢复快照。"""
    workflow = [
        {"id": "start", "type": "Start"},
        {"id": "answer", "type": "Answer"},
    ]
    runner, run = _runner_with_paused_run(workflow)
    events = list(runner.resume_events(
        run=run, agent=SimpleNamespace(id=1, workspace_id=1),
        chat_session=SimpleNamespace(id=1, user_id=1), user_message="hi", approved=False,
    ))

    assert run.status == "rejected"
    assert run.resume_state == {}
    assert [e["event"] for e in events] == ["rejected"]


def test_resume_requires_paused_state():
    """没有恢复快照 / 不在暂停中的 Run 不能恢复。"""
    runner = WorkflowRunner(db=SimpleNamespace(commit=lambda: None))
    try:
        list(runner.resume_events(
            run=SimpleNamespace(id=1, status="paused", resume_state={}),
            agent=SimpleNamespace(id=1), chat_session=SimpleNamespace(id=1, user_id=1),
            user_message="hi",
        ))
    except ValueError as exc:
        assert "恢复" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    try:
        list(runner.resume_events(
            run=SimpleNamespace(id=1, status="succeeded", resume_state={"context": {}}),
            agent=SimpleNamespace(id=1), chat_session=SimpleNamespace(id=1, user_id=1),
            user_message="hi",
        ))
    except ValueError as exc:
        assert "暂停" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_pause_snapshot_keeps_evidence_and_skips_internal_keys():
    """暂停快照保留已检索的证据与工具结果，丢弃下划线开头的运行时缓存。"""
    from core.runtime.workflow import _pause_snapshot

    workflow = [
        {"id": "start", "type": "Start", "next": "approval"},
        {"id": "approval", "type": "HumanApproval", "next": "answer"},
        {"id": "answer", "type": "Answer"},
    ]
    context = {
        "input": "退款怎么申请",
        "sources": [{"title": "手册", "snippet": "7 天内可退"}],
        "tool_outputs": [{"tool": "order_status", "content": "已发货"}],
        "_skill_metadata": {"huge": "cache"},
    }
    snapshot = _pause_snapshot(workflow, context, workflow[1], "draft", {"start", "approval"}, 1)

    assert snapshot["context"]["sources"] == [{"title": "手册", "snippet": "7 天内可退"}]
    assert snapshot["context"]["tool_outputs"][0]["content"] == "已发货"
    assert "_skill_metadata" not in snapshot["context"]
    assert snapshot["next_index"] == 2
    assert snapshot["executed"] == ["approval", "start"]


def test_pause_snapshot_uses_caller_index_not_value_lookup():
    """
    两个结构完全相同的节点：快照必须按调用方给的索引定位。

    早先用 `workflow.index(node)` 是按值相等查找，同构节点会命中第一个，
    恢复时就从错误的位置接着跑。
    """
    from core.runtime.workflow import _pause_snapshot

    workflow = [
        {"id": "start", "type": "Start", "next": "a"},
        {"id": "a", "type": "HumanApproval", "next": "answer"},
        # 除了 id 之外与上一个节点完全相同
        {"id": "b", "type": "HumanApproval", "next": "answer"},
        {"id": "answer", "type": "Answer"},
    ]
    ctx = {"input": "hi"}
    executed = {"start", "a", "b"}

    # 暂停在索引 2（节点 b）：下一跳应指向 answer（索引 3）
    snapshot = _pause_snapshot(workflow, ctx, workflow[2], "draft", executed, 2)
    assert snapshot["next_index"] == 3
    assert snapshot["paused_node_id"] == "b"
