"""Agent 端到端评测:任务完成率 / 工具调用正确率指标 + CI 自测门禁 + 真实跑模式。

- --check:CI 自测门禁,验证用例格式 + 指标逻辑自洽(不跑真实 agent)。
- --live:用真实 LLM(tars-ai)跑每个用例,收集 actual_tool_calls + answer,算指标。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CASES_PATH = Path(__file__).parent / "agent_cases.jsonl"


def load_cases(path: Path = CASES_PATH) -> list[dict]:
    """加载评测用例集(每行一个 JSON)。"""
    cases: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            cases.append(json.loads(line))
    return cases


def _has_call(expected: dict, actual_calls: list[dict]) -> bool:
    """单个 expected_tool_call 是否被实际调用:name 匹配 + args_subset 是 actual args 的子集(模糊含)。"""
    ename = expected.get("name")
    args_subset = expected.get("args_subset") or {}
    for a in actual_calls:
        if a.get("name") != ename:
            continue
        aargs = a.get("args") or {}
        if not args_subset:
            return True
        if all(str(v) in str(aargs.get(k, "")) or aargs.get(k) == v for k, v in args_subset.items()):
            return True
    return False


def evaluate_case(case: dict, actual_tool_calls: list[dict], answer: str) -> dict:
    """单个用例指标。

    actual_tool_calls: [{name, args}] —— ReAct 循环里实际调过的工具。
    - tool_correct:expected_tool_calls(带 args 子集)全部命中 actual;无则回退 expected_tools(旧 name 子集)。
    - answer_hit:reference_goal(新)在 answer 中;无则回退 expected_answer_contains(旧)。
    - completed:answer 非空且命中。
    """
    raw = actual_tool_calls or []
    actual_calls = [c if isinstance(c, dict) else {"name": c, "args": {}} for c in raw]
    actual_names = [c["name"] for c in actual_calls if c.get("name")]

    expected_calls = case.get("expected_tool_calls") or []
    if expected_calls:
        tool_correct = all(_has_call(c, actual_calls) for c in expected_calls)
    else:
        expected_tools = set(case.get("expected_tools") or [])
        tool_correct = expected_tools.issubset(set(actual_names)) if expected_tools else True

    ref = case.get("reference_goal")
    if ref is not None:
        answer_hit = ref in (answer or "")
    else:
        expected_contains = case.get("expected_answer_contains") or []
        answer_hit = any(p in (answer or "") for p in expected_contains) if expected_contains else True

    completed = bool(answer) and answer_hit
    missing = [c["name"] for c in expected_calls if not _has_call(c, actual_calls)] if expected_calls else []
    return {
        "id": case.get("id"),
        "tool_correct": tool_correct,
        "answer_hit": answer_hit,
        "completed": completed,
        "missing_tools": sorted(set(missing)),
    }


def evaluate_all(cases: list[dict], run_results: dict) -> dict:
    """汇总均指标。run_results: {case_id: (actual_tool_calls, answer)}。"""
    results = [evaluate_case(c, *run_results.get(c.get("id"), ([], ""))) for c in cases]
    n = len(results) or 1
    return {
        "total": len(results),
        "tool_correct_rate": sum(1 for r in results if r["tool_correct"]) / n,
        "answer_hit_rate": sum(1 for r in results if r["answer_hit"]) / n,
        "completion_rate": sum(1 for r in results if r["completed"]) / n,
        "results": results,
    }


def check() -> dict:
    """CI 自测门禁:验证用例格式 + 指标逻辑自洽(用预期喂回应 100% 通过)。"""
    cases = load_cases()
    assert cases, "no agent eval cases"
    for c in cases:
        assert "id" in c and "input" in c, f"bad case (missing id/input): {c}"
        assert isinstance(c.get("expected_tool_calls", []), list), f"bad case (expected_tool_calls not list): {c.get('id')}"
        assert "reference_goal" in c, f"bad case (missing reference_goal): {c.get('id')}"
    # 自洽:用预期工具调用 + 参考答案喂回,应全通过
    run_results = {
        c["id"]: (c.get("expected_tool_calls") or [], c.get("reference_goal") or "ok")
        for c in cases
    }
    summary = evaluate_all(cases, run_results)
    assert summary["completion_rate"] == 1.0, f"self-check failed: {summary}"
    print(f"agent eval check passed: {summary['total']} cases, completion_rate=1.0")
    return summary


def run_live() -> dict:
    """真实 LLM(tars-ai glm-5.2)跑每个用例,收集 actual_tool_calls + answer,算指标。

    复用 init_db + 默认 agent 的 setup(同 real_llm_smoke),不依赖外部 MySQL/Redis。
    """
    import os
    import sys

    sys.path.insert(0, os.getcwd())

    if not os.path.exists(CASES_PATH):
        raise FileNotFoundError(CASES_PATH)

    os.environ.setdefault("DATABASE_URL", "sqlite:///./tmp_agent_eval.db")
    os.environ.setdefault("OPENAI_API_BASE", "https://console.tars-ai.com/v1")
    os.environ.setdefault("OPENAI_API_KEY", os.environ.get("TARS_API_KEY", ""))
    os.environ.setdefault("OPENAI_MODEL", "4WWBCKTX/glm-5.2")
    os.environ.setdefault("LINGSHU_MOCK_LLM", "false")
    os.environ.setdefault("LINGSHU_VECTOR_BACKEND", "memory")
    os.environ.setdefault("REDIS_URL", "")
    os.environ.setdefault("CELERY_ENABLED", "false")
    os.environ.setdefault("HEALTH_MODEL_PROBE_ENABLED", "false")

    import core.config

    core.config.get_settings.cache_clear()

    from core.db.session import init_db, SessionLocal
    from core.db.models import Agent, User, Workspace, Session as ChatSession, WorkflowDefinition
    from core.runtime.workflow import WorkflowRunner, default_workflow
    from core.services.bootstrap import create_default_workspace_user

    if os.path.exists("./tmp_agent_eval.db"):
        os.remove("./tmp_agent_eval.db")
    init_db()
    db = SessionLocal()
    create_default_workspace_user(db, email="eval@test", name="eval", password="eval123")
    ws = db.query(Workspace).order_by(Workspace.id.asc()).first()
    user = db.query(User).order_by(User.id.asc()).first()
    agent = Agent(
        workspace_id=ws.id,
        model_id=1,
        name="eval-agent",
        system_prompt="你是简洁助手,回答尽量短。",
        model="4WWBCKTX/glm-5.2",
        status="draft",
        created_by=user.id,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    db.add(WorkflowDefinition(agent_id=agent.id, nodes=default_workflow()))
    db.commit()
    runner = WorkflowRunner(db)

    cases = load_cases()
    run_results: dict = {}
    for case in cases:
        session = ChatSession(workspace_id=ws.id, agent_id=agent.id, user_id=user.id, title=case["id"], is_debug=True)
        db.add(session)
        db.commit()
        db.refresh(session)
        answer = ""
        actual_calls: list[dict] = []
        try:
            for e in runner.run_events(
                agent=agent,
                chat_session=session,
                user_message=case["input"],
                mode="draft",
                rag_enabled=False,
            ):
                ev = e.get("event")
                if ev == "complete":
                    answer = e.get("answer", "")
                elif ev == "step":
                    for te in e["step"].get("events", []):
                        if te.get("event") == "tool_call":
                            actual_calls.append({"name": te["data"].get("tool_name"), "args": {}})
        except Exception as exc:  # noqa: BLE001 - 评测容错,单 case 失败不中断
            answer = f"[error: {exc}]"
        run_results[case["id"]] = (actual_calls, answer)
        print(f"  {case['id']}: {answer[:70]!r}")

    summary = evaluate_all(cases, run_results)
    print("\n=== live 评测汇总 ===")
    print(f"total={summary['total']} tool_correct_rate={summary['tool_correct_rate']:.0%} answer_hit_rate={summary['answer_hit_rate']:.0%} completion_rate={summary['completion_rate']:.0%}")
    for r in summary["results"]:
        flag = "OK" if r["completed"] else "MISS"
        print(f"  [{flag}] {r['id']}: tool_correct={r['tool_correct']} answer_hit={r['answer_hit']} missing={r['missing_tools']}")
    db.close()
    if os.path.exists("./tmp_agent_eval.db"):
        os.remove("./tmp_agent_eval.db")
    return summary


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--check"
    if mode == "--check":
        check()
    elif mode == "--live":
        run_live()
    else:
        print(f"unknown mode: {mode}; use --check or --live", file=sys.stderr)
        sys.exit(2)
