"""Skill versions, safe imports, and real provider packets for progressive loading."""
import base64
import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from core.db.base import Base
from core.db.models import User, Workspace, WorkspaceMember, Agent, McpServer, AgentMcpBinding, SkillVersion
from core.runtime.workflow import WorkflowRunner
from core.runtime.capabilities import CapabilityRouter, LOAD_SKILL, READ_FILE
from core.runtime.capabilities import validate_arguments, RUN_SCRIPT, SEARCH_TOOL
from core.services import skills
from core.integrations import llm

SOURCE = '---\nname: weekly-report\ndescription: Generate weekly reports\n---\n\nINSTRUCTION_BODY_SENTINEL: use references/format.md, then query echo tools.'


@pytest.fixture
def skill_db():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email='skill@example.test', name='Owner', password_hash='test')
        workspace = Workspace(name='Skill', slug='skill-test')
        db.add_all([user, workspace])
        db.flush()
        member = WorkspaceMember(user_id=user.id, workspace_id=workspace.id, role='admin')
        agent = Agent(workspace_id=workspace.id, created_by=user.id, name='Skill Agent')
        db.add_all([member, agent])
        db.commit()
        yield db, member, agent
    engine.dispose()


def package(**files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return base64.b64encode(buffer.getvalue()).decode()


def test_versions_are_pinned_and_files_are_lazy(skill_db):
    db, member, agent = skill_db
    skill = skills.save_skill(db, member, {'name': 'Weekly', 'package_base64': package(**{'weekly-report/SKILL.md': SOURCE, 'weekly-report/references/format.md': 'REFERENCE_BODY_SENTINEL'})})
    old_version = skill.current_version_id
    skills.replace_skill_bindings(db, member, agent.id, [{'skill_id': skill.id, 'version_id': old_version}])
    skills.save_skill(db, member, {'instructions': SOURCE.replace('INSTRUCTION_BODY_SENTINEL', 'NEW_BODY')}, skill)
    runtime = SimpleNamespace(id=agent.id, workspace_id=agent.workspace_id, skill_bindings=skills.skill_bindings(db, agent.id), settings={})
    router = CapabilityRouter(db, runtime, {'user_id': member.user_id}, [])
    metadata = router.skill_metadata()
    assert metadata[0]['version'] == 1
    assert 'INSTRUCTION_BODY_SENTINEL' not in json.dumps(metadata)
    with pytest.raises(ValueError, match='load_skill'):
        router.invoke(READ_FILE, {'skill_id': skill.id, 'path': 'references/format.md'})
    loaded = router.invoke(LOAD_SKILL, {'skill_id': skill.id})
    assert 'INSTRUCTION_BODY_SENTINEL' in loaded['content']
    assert 'REFERENCE_BODY_SENTINEL' not in loaded['content']
    assert 'REFERENCE_BODY_SENTINEL' in router.invoke(READ_FILE, {'skill_id': skill.id, 'path': 'references/format.md'})['content']
    skill.enabled = False
    db.commit()
    with pytest.raises(ValueError, match='停用'):
        router.invoke(READ_FILE, {'skill_id': skill.id, 'path': 'references/format.md'})


@pytest.mark.parametrize('path', ['../outside.txt', 'C:/outside.txt', '/outside.txt', 'x/../outside.txt'])
def test_zip_path_traversal_is_rejected(path):
    with pytest.raises(ValueError):
        skills.unpack_package(package(**{'SKILL.md': SOURCE, path: 'bad'}))


def test_skill_isolation_and_script_approval(skill_db):
    db, member, agent = skill_db
    skill = skills.save_skill(db, member, {'instructions': SOURCE})
    stranger = WorkspaceMember(workspace_id=member.workspace_id, user_id=999, role='user')
    with pytest.raises(ValueError, match='访问权限'):
        skills.accessible_skill(db, stranger, skill.id)
    member.role = 'user'
    db.commit()
    with pytest.raises(ValueError, match='管理员'):
        skills.save_skill(db, member, {'scripts_approved': True}, skill)
    assert db.get(SkillVersion, skill.current_version_id).scripts_approved is False


def test_control_arguments_and_external_schema_refs_are_rejected():
    with pytest.raises(ValueError, match='工具参数'):
        validate_arguments(LOAD_SKILL.schema['input_schema'], {})
    with pytest.raises(ValueError, match='外部引用'):
        validate_arguments({'$ref': 'https://127.0.0.1/private-schema'}, {})


@pytest.mark.parametrize('name', ['tool_search', 'load_skill', 'read_skill_file', 'run_skill_script'])
@pytest.mark.parametrize('dynamic', [False, True])
def test_existing_tools_with_control_names_remain_callable(skill_db, name, dynamic):
    db, member, agent = skill_db
    ordinary = SimpleNamespace(id=123, name=name, type='http', schema={}, description='Existing resource')
    other = SimpleNamespace(id=124, name='resource_tool_0', type='http', schema={})
    runtime = SimpleNamespace(workspace_id=member.workspace_id, settings={},
                              skill_bindings=[{'skill_id': 999, 'version_id': 999}] if dynamic else [])
    calls = []
    router = CapabilityRouter(db, runtime, {'user_id': member.user_id}, [ordinary, other],
                              executor=lambda tool, context: calls.append((tool, context['input'])) or {'content': 'resource result'})
    exposed = router.tools()
    assert len({tool.name for tool in exposed}) == len(exposed)
    if dynamic:
        assert LOAD_SKILL in exposed
        assert SEARCH_TOOL not in exposed
        if name != 'tool_search':
            assert exposed[0].name not in {'load_skill', 'read_skill_file', 'run_skill_script'}
    assert router.invoke(exposed[0], {'text': 'hello'})['content'] == 'resource result'
    assert calls == [(ordinary, {'text': 'hello'})]


@pytest.mark.parametrize('value', ['.nan', '.inf', '-.inf'])
def test_nonfinite_skill_metadata_is_rejected(value):
    source = SOURCE.replace('description: Generate weekly reports', f'description: Generate weekly reports\ncustom: {value}')
    with pytest.raises(ValueError, match='JSON'):
        skills.parse_instructions(source)


@pytest.mark.parametrize('query', ['看看我的仓库', '我的仓库', 'list repositories', 'GitHub', 'authenticated user', 'get_me'])
def test_tool_search_finds_identity_and_repository_capabilities(skill_db, monkeypatch, query):
    db, member, agent = skill_db
    definitions = [
        {'name': 'get_me', 'description': 'Get details of the authenticated GitHub user.', 'annotations': {'readOnlyHint': True}, 'inputSchema': {'type': 'object'}},
        {'name': 'search_repositories', 'description': 'Find GitHub repositories by name or metadata.', 'annotations': {'readOnlyHint': True}, 'inputSchema': {'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query']}},
        *[{'name': f'add_comment_{index}', 'description': 'Add a comment to a GitHub repository.', 'inputSchema': {'type': 'object', 'required': ['owner', 'repo']}} for index in range(47)],
        {'name': 'hidden_tool', 'description': 'Hidden repository tool', 'inputSchema': {'type': 'object'}},
    ]
    server = McpServer(workspace_id=member.workspace_id, created_by=member.user_id, name='g', transport='streamable_http', is_listed=False,
                       url='https://api.githubcopilot.com/mcp/', catalog={'tools': definitions})
    db.add(server)
    db.commit()
    import core.integrations.mcp_client as mcp_client
    monkeypatch.setattr(mcp_client, 'get_mcp_client', lambda *args: pytest.fail('Discovery must not connect to MCP'))
    runtime = SimpleNamespace(id=agent.id, workspace_id=member.workspace_id, settings={}, skill_bindings=[], tool_ids=[],
                              mcp_bindings=[{'server_id': server.id, 'selected_tools': [item['name'] for item in definitions[:-1]]}])
    candidates = WorkflowRunner(db)._runtime_tools(runtime, {'type': 'Tool'})
    router = CapabilityRouter(db, runtime, {'user_id': member.user_id}, candidates)
    metadata = router.mcp_metadata()
    assert metadata == [{'id': server.id, 'name': 'g', 'description': '', 'host': 'api.githubcopilot.com', 'tool_count': 49}]
    assert 'inputSchema' not in json.dumps(metadata)
    assert 'get_me' not in json.dumps(metadata)
    names = [item['name'] for item in router.search({'query': query})['tools']]
    assert 'input_schema' not in json.dumps(router.search({'query': query}))
    assert all(tool.schema['input_schema'] for tool in router.active.values())
    if query not in {'authenticated user', 'get_me'}:
        assert f'mcp_{server.id}_search_repositories' in names[:2]
    if query in {'GitHub', 'authenticated user', 'get_me'}:
        assert f'mcp_{server.id}_get_me' in names[:2]
    if query in {'authenticated user', 'get_me'}:
        assert names[0] == f'mcp_{server.id}_get_me'
    assert f'mcp_{server.id}_hidden_tool' not in names
    assert names[0] in {f'mcp_{server.id}_get_me', f'mcp_{server.id}_search_repositories'}
    member.role = 'user'
    server.created_by = 999
    db.commit()
    assert router.mcp_metadata() == []
    assert router.search({'query': query})['tools'] == []


def test_script_loading_never_executes_and_requires_approval(skill_db, monkeypatch):
    db, member, agent = skill_db
    skill = skills.save_skill(db, member, {'package_base64': package(**{'SKILL.md': SOURCE, 'scripts/never.py': 'raise RuntimeError("must not execute")'})})
    skills.replace_skill_bindings(db, member, agent.id, [{'skill_id': skill.id, 'version_id': skill.current_version_id}])
    runtime = SimpleNamespace(id=agent.id, workspace_id=member.workspace_id, settings={}, skill_bindings=skills.skill_bindings(db, agent.id))
    router = CapabilityRouter(db, runtime, {'user_id': member.user_id}, [])
    import core.runtime.skill_sandbox as sandbox
    monkeypatch.setattr(sandbox, 'run_script', lambda *args: pytest.fail('Unapproved scripts must never execute'))
    assert 'instructions' in json.loads(router.invoke(LOAD_SKILL, {'skill_id': skill.id})['content'])
    with pytest.raises(ValueError, match='授权'):
        router.invoke(RUN_SCRIPT, {'skill_id': skill.id, 'path': 'scripts/never.py'})


def test_sandbox_missing_configuration_does_not_execute_on_host(monkeypatch):
    import core.runtime.skill_sandbox as sandbox
    monkeypatch.setattr(sandbox, 'get_settings', lambda: SimpleNamespace(skill_sandbox_image=''))
    monkeypatch.setattr(sandbox.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('Must not execute on host'))
    with pytest.raises(ValueError, match='沙箱未配置'):
        sandbox.run_script([], 'scripts/example.py', [])


@pytest.mark.parametrize('provider', ['openai-compatible', 'anthropic'])
def test_model_searches_then_loads_skill_then_calls_mcp(skill_db, monkeypatch, provider):
    from core.config import get_settings
    db, member, agent = skill_db
    skill = skills.save_skill(db, member, {'instructions': SOURCE, 'name': 'Weekly report'})
    skills.replace_skill_bindings(db, member, agent.id, [{'skill_id': skill.id, 'version_id': skill.current_version_id}])
    server = McpServer(workspace_id=member.workspace_id, created_by=member.user_id, name='Echo service', transport='stdio',
                       command=['fixture'], catalog={'tools': [{'name': 'echo', 'description': 'Echo text', 'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string'}}}},
                                                               {'name': 'hidden_tool', 'inputSchema': {'type': 'object'}}]})
    db.add(server)
    db.flush()
    db.add(AgentMcpBinding(agent_id=agent.id, mcp_server_id=server.id, config={'selected_tools': ['echo']}))
    db.commit()
    calls = []
    import core.integrations.mcp_client as mcp_client
    monkeypatch.setattr(mcp_client, 'get_mcp_client', lambda item: SimpleNamespace(
        call_tool_result=lambda name, args: calls.append((name, args)) or {'content': [{'type': 'text', 'text': 'echoed'}], 'isError': False}))
    monkeypatch.setenv('LINGSHU_MOCK_LLM', 'false')
    get_settings.cache_clear()
    captured = []
    alias = f'mcp_{server.id}_echo'
    responses = [('tool_search', {'query': 'echo'}), ('load_skill', {'skill_id': skill.id}), (alias, {'text': 'hello'})]

    class Response(io.BytesIO):
        status = 200
        headers = {}

    def opened(request, **kwargs):
        body = json.loads(request.data)
        captured.append(body)
        index = len(captured) - 1
        if provider == 'anthropic':
            if index < len(responses):
                name, arguments = responses[index]
                data = {'content': [{'type': 'thinking', 'thinking': 'reason', 'signature': f'signed-{index}'},
                                    {'type': 'tool_use', 'id': f'call-{index}', 'name': name, 'input': arguments}]}
            else:
                data = {'content': [{'type': 'text', 'text': 'complete'}]}
        else:
            if index < len(responses):
                name, arguments = responses[index]
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': f'call-{index}', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}]}
            else:
                message = {'role': 'assistant', 'content': 'complete'}
            data = {'choices': [{'message': message}]}
        return Response(json.dumps(data).encode())

    monkeypatch.setattr(llm, 'open_public_https', opened)
    monkeypatch.setattr(llm.urllib.request, 'urlopen', opened)
    runtime = SimpleNamespace(id=agent.id, workspace_id=member.workspace_id, system_prompt='Help with the task.', tool_ids=[],
                              skill_bindings=skills.skill_bindings(db, agent.id), mcp_bindings=[{'server_id': server.id, 'selected_tools': ['echo']}],
                              settings={}, model='fixture', temperature=0.2, runtime_config={'provider': provider, 'chat_model': 'fixture',
                              'base_url': 'https://models.example.test/v1', 'api_key': 'fixture-only-key', 'untrusted_base_url': True})
    try:
        result = WorkflowRunner(db)._execute_node(runtime, {'type': 'Tool'}, {'user_id': member.user_id, 'input': 'Use weekly report with echo'})
        assert result['draft'] == 'complete'
        def names(packet):
            return {item['name'] if provider == 'anthropic' else item['function']['name'] for item in packet['tools']}
        assert names(captured[0]) == {'tool_search', 'load_skill'}
        assert alias not in json.dumps(captured[0])
        assert 'Weekly report' in json.dumps(captured[0])
        assert 'Available MCP service metadata' in json.dumps(captured[0])
        assert 'Echo service' in json.dumps(captured[0])
        assert 'INSTRUCTION_BODY_SENTINEL' not in json.dumps(captured[0])
        assert alias in names(captured[1])
        assert 'hidden_tool' not in json.dumps(captured)
        assert 'INSTRUCTION_BODY_SENTINEL' in json.dumps(captured[2])
        assert len(captured) == 4
        assert calls == [('echo', {'text': 'hello'})]
        if provider == 'anthropic':
            assert captured[1]['messages'][1]['content'][0]['signature'] == 'signed-0'
        assert any('Weekly report' in event['data']['result_preview'] for event in result['events'])
    finally:
        get_settings.cache_clear()


def test_tool_trace_redacts_credentials():
    from core.runtime.workflow import _tool_trace
    trace = _tool_trace([{'event': 'tool_call', 'data': {'tool_name': 'example', 'status': 'success',
        'input_preview': json.dumps({'query': 'user:Alice', 'api_key': 'secret-key', 'nested': {'password': 'secret-password'}})}}])
    assert trace[0]['arguments']['query'] == 'user:Alice'
    assert 'secret-key' not in json.dumps(trace)
    assert 'secret-password' not in json.dumps(trace)


def test_mcp_adapter_preserves_declared_name_arguments_and_result(skill_db):
    db, member, agent = skill_db
    server = McpServer(workspace_id=member.workspace_id, created_by=member.user_id, name='External service', transport='streamable_http',
        url='https://api.githubcopilot.com/mcp/', catalog={'tools': [{'name': 'search_repositories', 'description': 'Search repositories',
        'inputSchema': {'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query']}}]})
    db.add(server)
    db.commit()
    runtime = SimpleNamespace(id=agent.id, workspace_id=member.workspace_id, settings={}, tool_ids=[], skill_bindings=[],
                              mcp_bindings=[{'server_id': server.id, 'selected_tools': ['search_repositories']}])
    calls = []
    original_result = {'content': '{"items":[]}', 'result_json': {'items': []}}
    def execute(tool, context):
        calls.append((tool.schema['tool_name'], context['input']))
        return original_result
    router = CapabilityRouter(db, runtime, {'user_id': member.user_id, 'input': 'List my repositories'},
        WorkflowRunner(db)._runtime_tools(runtime, {'type': 'Tool'}), executor=execute)
    assert [tool.name for tool in router.tools()] == ['tool_search']
    router.search({'query': 'repository'})
    arguments = {'query': 'user:current'}
    assert router.invoke(router.active[f'mcp_{server.id}_search_repositories'], arguments) is original_result
    assert calls == [('search_repositories', arguments)]


def test_discovery_lists_deferred_metadata_without_loading_schema(skill_db):
    db, member, agent = skill_db
    server = McpServer(workspace_id=member.workspace_id, created_by=member.user_id, name='Workspace', transport='stdio', command=['fixture'], catalog={'tools': [
        {'name': 'project_report', 'description': 'Read project report', 'inputSchema': {'type': 'object', 'properties': {'project_id': {'type': 'string'}}, 'required': ['project_id']}},
        {'name': 'workspace_context', 'description': 'Read current workspace context', 'inputSchema': {'type': 'object'}},
        {'name': 'unbound_private', 'description': 'Not authorized', 'inputSchema': {'type': 'object'}},
    ]})
    db.add(server)
    db.commit()
    runtime = SimpleNamespace(id=agent.id, workspace_id=member.workspace_id, settings={}, tool_ids=[], skill_bindings=[],
        mcp_bindings=[{'server_id': server.id, 'selected_tools': ['project_report', 'workspace_context']}])
    router = CapabilityRouter(db, runtime, {'user_id': member.user_id}, WorkflowRunner(db)._runtime_tools(runtime, {'type': 'Tool'}))
    result = router.search({'query': 'report'})
    assert [tool['name'] for tool in result['tools']] == [f'mcp_{server.id}_project_report']
    assert {tool['name'] for tool in result['available_tool_metadata']} == {f'mcp_{server.id}_project_report', f'mcp_{server.id}_workspace_context'}
    assert 'input_schema' not in str(result)
    assert 'unbound_private' not in str(result)
    assert f'mcp_{server.id}_workspace_context' not in {tool.name for tool in router.tools()}
    assert router.known_mcp_tool(f'mcp_{server.id}_workspace_context')
    assert not router.known_mcp_tool(f'mcp_{server.id}_unbound_private')
    router.search({'query': f'mcp_{server.id}_workspace_context'})
    assert f'mcp_{server.id}_workspace_context' in {tool.name for tool in router.tools()}


def test_discovery_does_not_force_execution_or_rewrite_answer(skill_db):
    db, member, agent = skill_db
    server = McpServer(workspace_id=member.workspace_id, created_by=member.user_id, name='Echo', transport='stdio',
        command=['fixture'], catalog={'tools': [{'name': 'echo', 'inputSchema': {'type': 'object'}}]})
    db.add(server)
    db.commit()
    runtime = SimpleNamespace(id=agent.id, workspace_id=member.workspace_id, system_prompt='Help.', settings={},
        tool_ids=[], skill_bindings=[], mcp_bindings=[{'server_id': server.id, 'selected_tools': ['echo']}],
        model='fixture', temperature=0.2, runtime_config=None)
    requests = []
    def chat(messages, **kwargs):
        requests.append(messages)
        return llm.ChatResponse(tool_calls=[{'id': 'search', 'function': {'name': 'tool_search', 'arguments': '{"query":"echo"}'}}]) if len(requests) == 1 else llm.ChatResponse(content='The service provides an echo tool.')
    runner = WorkflowRunner(db)
    runner.provider = SimpleNamespace(chat=chat)
    result = runner._execute_node(runtime, {'type': 'Tool'}, {'user_id': member.user_id, 'input': 'What tools are available?'})
    assert len(requests) == 2
    assert result['draft'] == 'The service provides an echo tool.'
    assert result['tool_trace'][0]['arguments'] == {'query': 'echo'}


def test_deferred_tool_is_discovered_then_invoked_with_context(skill_db, monkeypatch):
    db, member, agent = skill_db
    server = McpServer(workspace_id=member.workspace_id, created_by=member.user_id, name='Workspace', transport='stdio', command=['fixture'], catalog={'tools': [
        {'name': 'project_report', 'description': 'Read project report', 'inputSchema': {'type': 'object', 'properties': {'project_id': {'type': 'string'}}, 'required': ['project_id']}},
        {'name': 'workspace_context', 'description': 'Read current workspace context', 'inputSchema': {'type': 'object'}},
    ]})
    db.add(server)
    db.commit()
    runtime = SimpleNamespace(id=agent.id, workspace_id=member.workspace_id, system_prompt='Help.', settings={}, tool_ids=[], skill_bindings=[],
        mcp_bindings=[{'server_id': server.id, 'selected_tools': ['project_report', 'workspace_context']}], model='fixture', temperature=0.2, runtime_config=None)
    import core.runtime.workflow as workflow
    calls = []
    def execute(tool, context):
        calls.append((tool.schema['tool_name'], context['input']))
        return {'content': '{"project_id":"PROJECT_17"}' if tool.schema['tool_name'] == 'workspace_context' else 'Real report'}
    monkeypatch.setattr(workflow, 'execute_tool', execute)
    sequence = [('tool_search', {'query': 'report'}), (f'mcp_{server.id}_workspace_context', {}),
                ('tool_search', {'query': f'mcp_{server.id}_workspace_context'}), (f'mcp_{server.id}_workspace_context', {}),
                (f'mcp_{server.id}_project_report', {'project_id': 'PROJECT_17'})]
    packets = []
    def chat(messages, **kwargs):
        index = len(packets)
        packets.append(json.dumps(messages))
        if index >= len(sequence):
            return llm.ChatResponse(content='Real report')
        name, args = sequence[index]
        return llm.ChatResponse(tool_calls=[{'id': str(index), 'function': {'name': name, 'arguments': json.dumps(args)}}])
    runner = WorkflowRunner(db)
    runner.provider = SimpleNamespace(chat=chat)
    result = runner._execute_node(runtime, {'type': 'Tool'}, {'user_id': member.user_id, 'input': 'Continue that report.',
        'conversation_history': [{'role': 'user', 'content': 'Use the current workspace.'}, {'role': 'assistant', 'content': 'I will discover its project id.'}]})
    assert result['draft'] == 'Real report'
    assert calls == [('workspace_context', {}), ('project_report', {'project_id': 'PROJECT_17'})]
    assert 'available_tool_metadata' in packets[1]
    assert 'authorized but not registered' in packets[2]
    assert 'Use the current workspace.' in packets[0]
    assert result['tool_trace'][1]['error_code'] == 'tool_not_loaded'
