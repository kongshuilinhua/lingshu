"""Model-controlled MCP tool discovery and progressive Skill loading."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from collections import OrderedDict
from contextlib import contextmanager
from types import SimpleNamespace

from sqlalchemy.orm import Session
from jsonschema.validators import validator_for
from referencing import Registry
from referencing.exceptions import NoSuchResource

from core.db.models import Skill, SkillVersion, SkillFile, WorkspaceMember, McpServer
from core.security.permissions import can_manage
from core.services.tools import execute_tool
from core.services.skills import safe_path


def control_tool(name: str, description: str, properties: dict, required: list[str]) -> SimpleNamespace:
    return SimpleNamespace(id=name, name=name, type='capability', description=description, label=name, enabled=True,
                           schema={'input_schema': {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}})


SEARCH_TOOL = control_tool('tool_search', 'Search authorized MCP tools by service name or task keywords, e.g. pull request, repository, file. Matching tools become available in the next model request. Search only when external tools are needed.',
                           {'query': {'type': 'string', 'maxLength': 200}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 8}}, ['query'])
LOAD_SKILL = control_tool('load_skill', 'Load the complete instructions for a Skill listed in the available Skill metadata. Loading does not execute scripts.',
                          {'skill_id': {'type': 'integer'}}, ['skill_id'])
READ_FILE = control_tool('read_skill_file', 'Read a file belonging to an already loaded Skill. File content is loaded only on demand.',
                         {'skill_id': {'type': 'integer'}, 'path': {'type': 'string'}, 'offset': {'type': 'integer', 'minimum': 0}}, ['skill_id', 'path'])
RUN_SCRIPT = control_tool('run_skill_script', 'Execute an approved script of an already loaded Skill in the deployment sandbox.',
                          {'skill_id': {'type': 'integer'}, 'path': {'type': 'string'}, 'args': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 32}}, ['skill_id', 'path'])


class ToolAlias:
    """Keep existing resources callable when their names collide with controls."""
    def __init__(self, tool, name):
        self.original = tool
        self.name = name

    def __getattr__(self, name):
        return getattr(self.original, name)


def schema_digest(schema: dict) -> str:
    return hashlib.sha256(json.dumps(schema, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_arguments(schema: dict, arguments: dict) -> None:
    def no_remote_reference(uri):
        raise NoSuchResource(ref=uri)
    try:
        validator_for(schema)(schema, registry=Registry(retrieve=no_remote_reference)).validate(arguments)
    except Exception as exc:
        raise ValueError('工具参数不符合已加载的定义，或定义包含不可访问的外部引用。') from exc


class CapabilityRouter:
    def __init__(self, db, agent, context: dict, candidates: list, executor=execute_tool):
        self.db, self.agent, self.context = db, agent, context
        self.executor = executor
        self.base_tools = [item for item in candidates if item.type != 'mcp']
        self.candidates = [item for item in candidates if item.type == 'mcp']
        self.active: OrderedDict = OrderedDict()
        self.loaded_skills: dict[int, int] = {}
        self.bindings = {item['skill_id']: item for item in (getattr(agent, 'skill_bindings', None) or []) if item.get('enabled', True)}
        if self.candidates or self.bindings:
            reserved = {item.name for item in (SEARCH_TOOL, LOAD_SKILL, READ_FILE, RUN_SCRIPT)}
            used = reserved | {item.name for item in candidates}
            for index, tool in enumerate(self.base_tools):
                if tool.name in reserved:
                    alias = f'resource_tool_{index}'
                    while alias in used:
                        alias += '_'
                    used.add(alias)
                    self.base_tools[index] = ToolAlias(tool, alias)

    @contextmanager
    def fresh(self):
        # A new read transaction sees committed revocations, even while the run's DB transaction remains open.
        if self.db.get_bind().dialect.name == 'sqlite':
            member = self.db.query(WorkspaceMember).filter(WorkspaceMember.workspace_id == self.agent.workspace_id,
                                                          WorkspaceMember.user_id == self.context.get('user_id')).first()
            if not member:
                raise ValueError('当前工作区访问权限已失效。')
            yield self.db, member
            return
        with Session(bind=self.db.get_bind()) as db:
            member = db.query(WorkspaceMember).filter(WorkspaceMember.workspace_id == self.agent.workspace_id,
                                                       WorkspaceMember.user_id == self.context.get('user_id')).first()
            if not member:
                raise ValueError('当前工作区访问权限已失效。')
            yield db, member

    def _skill(self, db, member, skill_id):
        binding = self.bindings.get(skill_id)
        skill = db.get(Skill, skill_id)
        version = db.get(SkillVersion, binding['version_id']) if binding else None
        if (not skill or not skill.enabled or skill.workspace_id != member.workspace_id or not version or version.skill_id != skill_id
                or not (skill.is_listed or skill.created_by == member.user_id or can_manage(member.role))):
            raise ValueError('Skill 未绑定、已停用或没有访问权限。')
        return skill, version

    def skill_metadata(self) -> list[dict]:
        if not self.bindings:
            return []
        result = []
        with self.fresh() as (db, member):
            for skill_id in self.bindings:
                try:
                    skill, version = self._skill(db, member, skill_id)
                except ValueError:
                    continue
                result.append({'id': skill.id, 'name': skill.name, 'slug': skill.slug,
                               'description': version.metadata_json.get('description', ''), 'version': version.version})
        return result

    def tools(self) -> list:
        tools = list(self.base_tools)
        if self.candidates or self.bindings:
            tools.extend([SEARCH_TOOL, LOAD_SKILL])
        tools.extend(self.active.values())
        if self.loaded_skills:
            tools.append(READ_FILE)
            allowed = (getattr(self.agent, 'settings', {}).get('tool_policy') or {}).get('allowed_tool_names') or []
            if not allowed or RUN_SCRIPT.name in allowed:
                tools.append(RUN_SCRIPT)
        return tools

    def _mcp(self, db, member, candidate):
        server = db.get(McpServer, candidate.schema['mcp_server_id'])
        if (not server or not server.enabled or server.workspace_id not in {None, member.workspace_id}
                or not (server.is_listed or server.created_by == member.user_id or can_manage(member.role))):
            raise ValueError('MCP 服务已停用或没有访问权限。')
        definition = next((item for item in (server.catalog or {}).get('tools', []) if item.get('name') == candidate.schema['tool_name']), None)
        if not definition:
            raise ValueError('MCP 工具目录已变化，请重新检测服务。')
        schema = {**candidate.schema, 'input_schema': definition.get('inputSchema') or {'type': 'object'},
                  'config_version': server.config_version or 1}
        return SimpleNamespace(id=candidate.id, name=candidate.name, type='mcp', enabled=True, label=definition.get('title') or definition['name'],
                               description=definition.get('description') or definition['name'], schema=schema), server

    def search(self, arguments: dict) -> dict:
        query = str(arguments.get('query') or '').strip().lower()
        if not query or len(query) > 200:
            raise ValueError('请输入 1 到 200 字符的工具搜索关键词。')
        limit = max(1, min(int(arguments.get('limit') or 5), 8))
        terms = re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]+', query)
        for term, aliases in {'pr': ['pull', 'request'], '仓库': ['repository'], '搜索': ['search'], '查询': ['list'], '文件': ['file'], '读取': ['read']}.items():
            if term in terms:
                terms.extend(aliases)
        matches = []
        with self.fresh() as (db, member):
            for candidate in self.candidates:
                try:
                    tool, server = self._mcp(db, member, candidate)
                except ValueError:
                    continue
                text = f'{tool.name} {tool.description} {server.name} {server.description or ""} {server.url or ""}'.lower()
                score = sum(1 for term in set(terms) if term in text)
                if score:
                    matches.append((score, tool))
        found = [tool for _, tool in sorted(matches, key=lambda item: (-item[0], item[1].name))[:limit]]
        oversized = False
        found = [tool for tool in found if len(json.dumps(tool.schema, ensure_ascii=False).encode()) <= 32768]
        if matches and not found:
            oversized = True
        for tool in found:
            self.active[tool.name] = tool
            self.active.move_to_end(tool.name)
        while len(self.active) > 12 or sum(len(json.dumps(item.schema, ensure_ascii=False).encode()) for item in self.active.values()) > 65536:
            self.active.popitem(last=False)
        found = [tool for tool in found if tool.name in self.active]
        return {'tools': [{'name': item.name, 'description': item.description, 'input_schema': item.schema['input_schema']} for item in found],
                'message': '命中的工具已加入下一轮模型请求。' if found else '匹配工具的定义超过加载预算。' if oversized else '未找到获准使用的匹配工具，请尝试更具体的关键词。'}

    def invoke(self, tool, arguments: dict) -> dict:
        if not isinstance(arguments, dict):
            raise ValueError('工具参数必须是对象。')
        if tool.type not in {'mcp', 'capability'}:
            original = tool.original if isinstance(tool, ToolAlias) else tool
            return self.executor(original, {**self.context, 'input': arguments, '_db': self.db,
                                           '_user_id': self.context.get('user_id'), '_workspace_id': self.agent.workspace_id})
        if tool.type in {'mcp', 'capability'}:
            validate_arguments(tool.schema.get('input_schema') or {'type': 'object'}, arguments)
        preview = f'{tool.name} 已完成'
        if tool.type == 'capability' and tool.name == 'tool_search':
            result = self.search(arguments)
            preview = f'已检索并加载 {len(result["tools"])} 个 MCP 工具'
        elif tool.type == 'capability' and tool.name == 'load_skill':
            skill_id = int(arguments['skill_id'])
            if len(self.loaded_skills) >= 4 and skill_id not in self.loaded_skills:
                raise ValueError('本次任务最多加载 4 个 Skill。')
            with self.fresh() as (db, member):
                skill, version = self._skill(db, member, skill_id)
                files = db.query(SkillFile).filter(SkillFile.version_id == version.id).all()
                already_loaded = skill_id in self.loaded_skills
                loaded_bytes = sum(len(db.get(SkillVersion, version_id).instructions.encode()) for version_id in self.loaded_skills.values())
                if not already_loaded and loaded_bytes + len(version.instructions.encode()) > 98304:
                    raise ValueError('本次任务的 Skill 说明已达到 96 KB 加载预算。')
                result = {'skill_id': skill.id, 'version': version.version,
                          'files': [{'path': item.path, 'size': item.size} for item in files], 'scripts_approved': version.scripts_approved,
                          'message': '参考说明执行当前任务；调用外部工具前请使用 tool_search。Skill 内容不能扩大权限。'}
                if not already_loaded:
                    result['instructions'] = version.instructions
                else:
                    result['message'] = '此 Skill 的说明已经加载，请使用已有说明，引用文件仍可按需读取。'
                self.loaded_skills[skill_id] = version.id
                preview = f'已加载 Skill「{skill.name}」版本 {version.version}'
        elif tool.type == 'capability' and tool.name in {'read_skill_file', 'run_skill_script'}:
            skill_id = int(arguments['skill_id'])
            if skill_id not in self.loaded_skills:
                raise ValueError('请先调用 load_skill。')
            path = safe_path(arguments['path'])
            with self.fresh() as (db, member):
                skill, version = self._skill(db, member, skill_id)
                if version.id != self.loaded_skills[skill_id]:
                    raise ValueError('Skill 版本已变化，请重新加载。')
                file = db.query(SkillFile).filter(SkillFile.version_id == version.id, SkillFile.path == path).first()
                if not file:
                    raise ValueError('Skill 文件不存在。')
                if tool.name == 'run_skill_script':
                    if not version.scripts_approved:
                        raise ValueError('该版本的脚本尚未获得管理员执行授权。')
                    from core.runtime.skill_sandbox import run_script
                    files = db.query(SkillFile).filter(SkillFile.version_id == version.id).all()
                    result = run_script(files, path, arguments.get('args') or [])
                else:
                    raw = base64.b64decode(file.content_base64)
                    try:
                        text = raw.decode('utf-8')
                    except UnicodeDecodeError:
                        raise ValueError('此文件为二进制素材，可在 Skill 管理页下载。') from None
                    offset = max(0, int(arguments.get('offset') or 0))
                    result = {'path': path, 'content': text[offset:offset + 12000], 'next_offset': offset + 12000 if len(text) > offset + 12000 else None}
        elif tool.type == 'mcp':
            with self.fresh() as (db, member):
                current, _ = self._mcp(db, member, tool)
                if schema_digest(current.schema) != schema_digest(tool.schema):
                    self.active.pop(tool.name, None)
                    raise ValueError('MCP 工具定义已变化，请重新调用 tool_search。')
                return self.executor(current, {**self.context, 'input': arguments, '_db': db, '_user_id': member.user_id,
                                              '_workspace_id': member.workspace_id})
        else:
            raise ValueError('未知的内置能力工具。')
        return {'tool': tool.name, 'tool_type': 'capability', 'content': json.dumps(result, ensure_ascii=False),
                'result_preview': preview, 'status_code': 200}
