from types import SimpleNamespace

from core.runtime.workflow import WorkflowRunner
from core.services.agents import normalize_tool_policy


def test_search_permission_is_agent_owned_and_requests_cannot_enable_it():
    runner = WorkflowRunner(SimpleNamespace())
    disabled = SimpleNamespace(settings={'tool_policy': {'web_search_enabled': False}})
    enabled = SimpleNamespace(settings={'tool_policy': {'web_search_enabled': True}})
    assert runner._agent_search_status(disabled, 'news', True)['reason'] == 'agent_disabled'
    assert runner._agent_search_status(enabled, 'news', None)['enabled'] is True
    assert runner._agent_search_status(enabled, 'news', False)['reason'] == 'not_requested'
    assert normalize_tool_policy({})['web_search_enabled'] is False


def test_enabling_search_does_not_search_until_the_model_calls_a_tool(monkeypatch):
    from core.services import web_search
    monkeypatch.setattr(web_search, 'search_web', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('No eager network search')))
    runner = WorkflowRunner(SimpleNamespace())
    status = runner._agent_search_status(SimpleNamespace(settings={'tool_policy': {'web_search_enabled': True}}), 'hello', None)
    assert status['reason'] == 'ready' and status['items'] == []


def test_runtime_search_tool_respects_agent_toggle():
    class EmptyQuery:
        def filter(self, *args): return self
        def all(self): return []
    runner = WorkflowRunner(SimpleNamespace(query=lambda *args: EmptyQuery()))
    for enabled in [False, True]:
        agent = SimpleNamespace(id=1, tool_ids=[], mcp_bindings=[], settings={'tool_policy': {'web_search_enabled': enabled}})
        tools = runner._runtime_tools(agent, {'type': 'Tool'})
        assert any(tool.name == 'web_search' for tool in tools) is enabled
