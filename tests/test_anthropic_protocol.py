import io
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from core.integrations import anthropic_protocol as protocol
from core.integrations import llm
from core.integrations.langchain_provider import get_chat_model
from core.runtime.workflow import WorkflowRunner
from core.services.user_models import probe_models_payload


class Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


@pytest.fixture
def live_settings(monkeypatch):
    settings = SimpleNamespace(mock_llm=False, llm_max_tokens=8192, openai_model='default',
        circuit_breaker_distributed=False, anthropic_api_key='server-only-test-key',
        anthropic_api_base='https://api.anthropic.com/v1')
    monkeypatch.setattr(llm, 'get_settings', lambda: settings)
    monkeypatch.setattr('core.config.get_settings', lambda: settings)
    return settings


RUNTIME = {'provider': 'anthropic', 'base_url': 'https://example.com/v1',
           'api_key': 'test-only-key', 'chat_model': 'claude-test', 'untrusted_base_url': True}
TOOL = {'type': 'function', 'function': {'name': 'echo', 'description': 'Echo',
         'parameters': {'type': 'object', 'properties': {'text': {'type': 'string'}}}}}


@pytest.mark.parametrize('base', ['https://example.com', 'https://example.com/v1/'])
def test_native_endpoint_joins_version_once(base):
    assert protocol.endpoint(base, 'messages') == 'https://example.com/v1/messages'


def test_system_tool_round_and_signed_blocks_are_preserved():
    signed = [{'type': 'thinking', 'thinking': 'summary', 'signature': 'opaque-signature'},
              {'type': 'tool_use', 'id': 'toolu_a', 'name': 'echo', 'input': {'text': 'hi'}}]
    body = protocol.request_body([
        {'role': 'system', 'content': 'system prompt'}, {'role': 'user', 'content': 'hi'},
        {'role': 'assistant', 'content': None, 'anthropic_content': signed},
        {'role': 'tool', 'tool_call_id': 'toolu_a', 'content': 'done'},
        {'role': 'tool', 'tool_call_id': 'toolu_b', 'content': 'also done'},
    ], model='claude-test', max_tokens=2048, tools=[TOOL])
    assert body['system'] == [{'type': 'text', 'text': 'system prompt'}]
    assert body['messages'][1]['content'] == signed
    assert len(body['messages'][2]['content']) == 2
    assert body['messages'][2]['content'][0]['tool_use_id'] == 'toolu_a'
    assert body['tools'][0]['input_schema'] == TOOL['function']['parameters']
    assert 'temperature' not in body and 'system' not in [item['role'] for item in body['messages']]


def test_image_conversion_and_invalid_base64():
    blocks = protocol.content_blocks([{'type': 'text', 'text': 'look'},
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,aGk='}},
        {'type': 'image_url', 'image_url': {'url': 'https://example.com/image.png'}}])
    assert blocks[1]['source'] == {'type': 'base64', 'media_type': 'image/png', 'data': 'aGk='}
    assert blocks[2]['source']['type'] == 'url'
    with pytest.raises(ValueError):
        protocol.content_blocks([{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,!!'}}])


def test_live_chat_uses_native_headers_endpoint_and_public_transport(live_settings, monkeypatch):
    captured = []

    def opened(request, **kwargs):
        captured.append(request)
        return Response(json.dumps({'content': [{'type': 'text', 'text': 'hi'},
            {'type': 'tool_use', 'id': 'toolu_1', 'name': 'echo', 'input': {'text': 'ok'}}]}).encode())

    monkeypatch.setattr(llm, 'open_public_https', opened)
    response = llm.OpenAICompatibleProvider().chat([{'role': 'user', 'content': 'hi'}], runtime_config=RUNTIME, tools=[TOOL])
    request = captured[0]
    assert request.full_url == 'https://example.com/v1/messages'
    assert request.get_header('X-api-key') == 'test-only-key'
    assert request.get_header('Anthropic-version') == '2023-06-01'
    assert request.get_header('Authorization') is None
    assert json.loads(request.data)['max_tokens'] == 8192
    assert response.content == 'hi'
    assert json.loads(response.tool_calls[0]['function']['arguments']) == {'text': 'ok'}


def sse(events):
    return Response(''.join('event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n' for event in events).encode())


def test_native_stream_separates_text_thinking_and_tool_arguments(live_settings, monkeypatch):
    events = [
        {'type': 'message_start', 'message': {}},
        {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'thinking_delta', 'thinking': 'plan'}},
        {'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'text_delta', 'text': 'answer'}},
        {'type': 'content_block_start', 'index': 2, 'content_block': {'type': 'tool_use', 'id': 'toolu_s', 'name': 'echo', 'input': {}}},
        {'type': 'content_block_delta', 'index': 2, 'delta': {'type': 'input_json_delta', 'partial_json': '{"text":'}},
        {'type': 'content_block_delta', 'index': 2, 'delta': {'type': 'input_json_delta', 'partial_json': '"ok"}'}},
        {'type': 'content_block_stop', 'index': 2}, {'type': 'message_stop'},
    ]
    monkeypatch.setattr(llm, 'open_public_https', lambda request, **kwargs: sse(events))
    frames = list(llm.OpenAICompatibleProvider().chat_stream([{'role': 'user', 'content': 'hi'}], runtime_config=RUNTIME))
    assert frames[:2] == [{'type': 'reasoning', 'text': 'plan'}, {'type': 'content', 'text': 'answer'}]
    assert frames[2]['tool_call']['id'] == 'toolu_s'
    assert json.loads(frames[2]['tool_call']['function']['arguments']) == {'text': 'ok'}


def test_stream_error_and_truncated_stream_are_not_successful_answers():
    with pytest.raises(RuntimeError, match='overloaded_error'):
        list(protocol.stream_frames(sse([{'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'hidden-key'}}])))
    with pytest.raises(RuntimeError, match='incomplete'):
        list(protocol.stream_frames(sse([{'type': 'ping'}])))


def test_http_error_does_not_expose_anthropic_key(live_settings, monkeypatch):
    import urllib.error

    def fail(request, **kwargs):
        raise urllib.error.HTTPError(request.full_url, 401, 'bad key', {}, io.BytesIO(b'test-only-key'))

    monkeypatch.setattr(llm, 'open_public_https', fail)
    with pytest.raises(RuntimeError) as error:
        llm.OpenAICompatibleProvider().chat([{'role': 'user', 'content': 'hi'}], runtime_config=RUNTIME)
    assert 'test-only-key' not in str(error.value)


def test_anthropic_model_list_pagination_and_auth(monkeypatch):
    captured = []

    def opened(request, **kwargs):
        captured.append(request)
        page = {'data': [{'id': 'claude-a'}], 'has_more': True, 'last_id': 'claude-a'} if len(captured) == 1 else {'data': [{'id': 'claude-b'}], 'has_more': False}
        return Response(json.dumps(page).encode())

    monkeypatch.setattr('core.services.user_models.open_public_https', opened)
    result = probe_models_payload(RUNTIME)
    assert result['models'] == ['claude-a', 'claude-b']
    assert captured[1].full_url.endswith('/v1/models?after_id=claude-a')
    assert captured[0].get_header('X-api-key') == 'test-only-key'
    assert captured[0].get_header('Authorization') is None


def test_langchain_structured_helpers_use_the_native_gateway(live_settings, monkeypatch):
    class Facts(BaseModel):
        facts: list[str]

    captured = []

    def opened(request, **kwargs):
        body = json.loads(request.data)
        captured.append(body)
        return Response(json.dumps({'content': [{'type': 'tool_use', 'id': 'toolu_f',
            'name': 'Facts', 'input': {'facts': ['remembered']}}]}).encode())

    monkeypatch.setattr(llm, 'open_public_https', opened)
    model = get_chat_model(runtime_config=RUNTIME)
    result = model.with_structured_output(Facts, method='function_calling').invoke([SystemMessage(content='Extract'), HumanMessage(content='hi')])
    assert result.facts == ['remembered']
    assert captured[0]['tool_choice'] == {'type': 'any'}
    assert 'test-only-key' not in str(model.model_dump())


def test_agent_native_tool_loop_replays_signed_content_and_results(live_settings, monkeypatch):
    signed = [{'type': 'thinking', 'thinking': 'summary', 'signature': 'opaque'},
              {'type': 'tool_use', 'id': 'toolu_echo', 'name': 'echo', 'input': {'text': 'hi'}}]
    captured = []

    def opened(request, **kwargs):
        captured.append(json.loads(request.data))
        blocks = signed if len(captured) == 1 else [{'type': 'text', 'text': 'done'}]
        return Response(json.dumps({'content': blocks}).encode())

    monkeypatch.setattr(llm, 'open_public_https', opened)
    runner = WorkflowRunner(SimpleNamespace())
    tool = SimpleNamespace(id=1, name='echo', label='echo', description='Echo', type='http',
                           schema={}, query_schema={'text': {'required': True}}, method='GET')
    monkeypatch.setattr(runner, '_runtime_tools', lambda *args: [tool])
    monkeypatch.setattr(runner, '_llm_messages', lambda *args: [{'role': 'user', 'content': 'hi'}])
    monkeypatch.setattr('core.runtime.workflow.execute_tool', lambda tool, ctx: {'tool': 'echo', 'tool_type': 'mcp', 'content': 'echoed'})
    agent = SimpleNamespace(settings={}, model='claude-test', temperature=0.4, runtime_config=RUNTIME, workspace_id=1)
    result = runner._execute_node(agent, {'type': 'Tool'}, {'user_id': 1})
    assert result['draft'] == 'done' and result['tool_stats']['total_calls'] == 1
    assert result['events'][0]['event'] == 'tool_call'
    assert captured[1]['messages'][1]['content'] == signed
    assert captured[1]['messages'][2]['content'][0]['tool_use_id'] == 'toolu_echo'
