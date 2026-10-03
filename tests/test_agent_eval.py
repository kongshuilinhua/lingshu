"""Agent 评测闭环测试:用例加载 + 指标计算(新字段 expected_tool_calls + reference_goal)+ CI 自测门禁。"""

from eval.agent_eval import check, evaluate_all, evaluate_case, load_cases


def test_load_cases_has_valid_structure():
    cases = load_cases()
    assert len(cases) >= 10
    for c in cases:
        assert "id" in c
        assert "input" in c
        assert isinstance(c.get("expected_tool_calls", []), list)
        assert "reference_goal" in c


def test_evaluate_case_tool_calls_with_args_subset_hit():
    case = {"id": "x", "expected_tool_calls": [{"name": "calculator", "args_subset": {"expression": "3+5"}}], "reference_goal": "8"}
    r = evaluate_case(case, [{"name": "calculator", "args": {"expression": "3+5"}}], "结果是8")
    assert r["tool_correct"] is True
    assert r["answer_hit"] is True
    assert r["completed"] is True
    assert r["missing_tools"] == []


def test_evaluate_case_tool_calls_args_mismatch():
    case = {"id": "x", "expected_tool_calls": [{"name": "calculator", "args_subset": {"expression": "3+5"}}], "reference_goal": "8"}
    # 调了 calculator 但参数不对
    r = evaluate_case(case, [{"name": "calculator", "args": {"expression": "2+2"}}], "4")
    assert r["tool_correct"] is False
    assert "calculator" in r["missing_tools"]


def test_evaluate_case_tool_calls_missing_tool():
    case = {"id": "x", "expected_tool_calls": [{"name": "calculator"}], "reference_goal": "8"}
    # 没调 calculator
    r = evaluate_case(case, [{"name": "current_time", "args": {}}], "现在")
    assert r["tool_correct"] is False
    assert "calculator" in r["missing_tools"]


def test_evaluate_case_reference_goal_hit():
    case = {"id": "x", "expected_tool_calls": [], "reference_goal": "首都"}
    r = evaluate_case(case, [], "北京是中国的首都")
    assert r["answer_hit"] is True
    assert r["completed"] is True


def test_evaluate_case_reference_goal_miss_blocks_completion():
    case = {"id": "x", "expected_tool_calls": [], "reference_goal": "北京"}
    r = evaluate_case(case, [], "上海")
    assert r["answer_hit"] is False
    assert r["completed"] is False


def test_evaluate_case_empty_answer_blocks_completion():
    case = {"id": "x", "expected_tool_calls": [], "reference_goal": "ok"}
    r = evaluate_case(case, [], "")
    assert r["completed"] is False


def test_evaluate_case_legacy_expected_tools_compat():
    # 旧字段 expected_tools + expected_answer_contains 向后兼容
    case = {"id": "x", "expected_tools": ["calc"], "expected_answer_contains": ["8"]}
    r = evaluate_case(case, ["calc"], "结果8")
    assert r["tool_correct"] is True
    assert r["answer_hit"] is True


def test_evaluate_all_summary_rates():
    cases = [
        {"id": "a", "expected_tool_calls": [{"name": "t"}], "reference_goal": "ok"},
        {"id": "b", "expected_tool_calls": [], "reference_goal": "no"},
    ]
    run_results = {"a": ([{"name": "t", "args": {}}], "ok"), "b": ([], "wrong")}
    summary = evaluate_all(cases, run_results)
    assert summary["total"] == 2
    assert summary["tool_correct_rate"] == 1.0
    assert summary["answer_hit_rate"] == 0.5
    assert summary["completion_rate"] == 0.5


def test_check_passes_as_ci_gate():
    summary = check()
    assert summary["completion_rate"] == 1.0
    assert summary["total"] >= 10
