"""知识库回答的「硬拒答 vs 交给模型」边界。

真实环境实测（bge-m3 + bge-reranker-v2-m3）：检索分数分不清「相关但打分低」和「真无关」——
「配送多远」手册里写着 3 公里，rerank 却是 0.00，与「星巴克价格」(0.018) 一样低。
所以只有确实没检索到任何片段（空库 / 全部停用）才硬拒答；检索到片段时一律交给模型，
并用提示词约束它只依据片段作答、片段没有就明说。
"""
from types import SimpleNamespace

import pytest

from core.runtime import workflow
from core.services.rag import RAG_NO_EVIDENCE_ANSWER, RagResult

SNIPPET = {"title": "运营手册", "snippet": "配送范围 3 公里以内，最低消费 50 元起。", "content": "配送范围 3 公里以内，最低消费 50 元起。",
           "parent_id": "p0", "score": 0.0, "dense_score": 0.47, "retrieval_channel": "rerank"}


@pytest.fixture
def run_knowledge(monkeypatch):
    def run(question, *, sources, no_evidence, refuse=True):
        status = {"knowledge_base_ids": [11], "no_evidence": no_evidence, "refuse_when_no_evidence": refuse,
                  "reason": "available" if sources else "no_match"}
        monkeypatch.setattr(workflow, "get_settings", lambda: SimpleNamespace(rag_self_correct=False))
        monkeypatch.setattr(workflow, "retrieve", lambda db, **kwargs: RagResult(list(sources), dict(status)))
        runner = workflow.WorkflowRunner.__new__(workflow.WorkflowRunner)
        runner.db = object()
        agent = SimpleNamespace(workspace_id=7, knowledge_base_ids=[11], runtime_config=None)
        return runner._execute_node(agent, {"type": "Knowledge", "config": {"top_k": 4}},
                                    {"input": question, "rag_enabled": True, "rag_config": {}})
    return run


def test_low_score_sources_go_to_model_instead_of_refusal(run_knowledge):
    output = run_knowledge("配送多远", sources=[SNIPPET], no_evidence=True)

    assert "draft" not in output
    assert not output.get("rag_refusal")
    assert output["sources"] == [SNIPPET]
    assert output["rag_grounded"] is True


def test_nothing_retrieved_is_still_refused_without_calling_model(run_knowledge):
    output = run_knowledge("配送多远", sources=[], no_evidence=True)

    assert output["draft"] == RAG_NO_EVIDENCE_ANSWER
    assert output["rag_refusal"] is True


def test_small_talk_drops_sources_and_is_not_refused(run_knowledge):
    output = run_knowledge("你好", sources=[SNIPPET], no_evidence=True)

    assert output["sources"] == []
    assert "draft" not in output
    assert output["rag_status"]["reason"] == "small_talk"


def test_refusal_disabled_keeps_sources_without_grounding(run_knowledge):
    output = run_knowledge("配送多远", sources=[SNIPPET], no_evidence=True, refuse=False)

    assert output["sources"] == [SNIPPET]
    assert not output.get("rag_grounded")


def _system_prompt(context):
    runner = workflow.WorkflowRunner.__new__(workflow.WorkflowRunner)
    runner.db = object()
    agent = SimpleNamespace(id=1, workspace_id=7, system_prompt="你是客服。", settings={}, skill_bindings=[])
    context = {"input": "配送多远", "_skill_metadata": [], **context}
    return runner._llm_messages(agent, context)[0]["content"]


def test_grounded_prompt_tells_model_to_answer_only_from_snippets():
    prompt = _system_prompt({"sources": [SNIPPET], "rag_grounded": True})

    assert "配送范围 3 公里以内" in prompt
    assert "只依据「可用知识片段」" in prompt


def test_prompt_has_no_grounding_rule_when_not_grounded():
    assert "只依据「可用知识片段」" not in _system_prompt({"sources": [SNIPPET]})
