"""Versioned, workspace-scoped Agent Skills and bounded package imports."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import stat
import zipfile
from pathlib import PurePosixPath

import yaml
from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.db.models import Skill, SkillVersion, SkillFile, AgentSkillBinding, AgentVersion, Agent, WorkspaceMember
from core.security.permissions import can_manage

MAX_PACKAGE_BYTES = 10 * 1024 * 1024


def safe_path(path: str) -> str:
    normalized = str(path).replace('\\', '/')
    if not normalized or normalized.startswith('/') or ':' in normalized or '\x00' in normalized or any(part in {'', '.', '..'} for part in normalized.split('/')):
        raise ValueError('Skill 文件路径不合法。')
    if len(normalized) > 500:
        raise ValueError('Skill 文件路径过长。')
    return normalized


def parse_instructions(text: str) -> tuple[dict, str]:
    if len(text.encode('utf-8')) > 48 * 1024:
        raise ValueError('SKILL.md 不能超过 48 KB，请将详细内容拆分到引用文件。')
    match = re.match(r'\A---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)(.*)\Z', text, re.S)
    if not match:
        raise ValueError('SKILL.md 需要 YAML 元数据头，包含 name 和 description。')
    try:
        metadata = yaml.safe_load(match.group(1))
    except (yaml.YAMLError, RecursionError) as exc:
        raise ValueError('Skill YAML 元数据不正确。') from exc
    if not isinstance(metadata, dict):
        raise ValueError('Skill 元数据必须是对象。')
    remaining = [300]
    def bounded(value, depth=0):
        remaining[0] -= 1
        if depth > 6 or remaining[0] < 0:
            raise ValueError('Skill 元数据层级或项目数量过多。')
        if isinstance(value, dict):
            for key, nested in value.items():
                if not isinstance(key, str):
                    raise ValueError('Skill 元数据键必须是字符串。')
                bounded(nested, depth + 1)
        elif isinstance(value, list):
            for nested in value:
                bounded(nested, depth + 1)
    bounded(metadata)
    name = metadata.get('name')
    description = metadata.get('description')
    if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name) or len(name) > 64:
        raise ValueError('Skill name 需为不超过 64 字符的小写英文、数字和连字符。')
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise ValueError('Skill description 必须为 1 到 1024 字符。')
    if not match.group(2).strip():
        raise ValueError('Skill 正文不能为空。')
    # Preserve portable metadata without accepting arbitrary YAML Python objects.
    try:
        json.dumps(metadata, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError('Skill 元数据必须可以序列化为 JSON。') from exc
    return metadata, match.group(2).strip()


def unpack_package(package_base64: str) -> tuple[str, dict[str, bytes]]:
    try:
        raw = base64.b64decode(package_base64, validate=True)
        if len(raw) > MAX_PACKAGE_BYTES:
            raise ValueError('Skill 压缩包不能超过 10 MB。')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = [item for item in archive.infolist() if not item.is_dir()]
            if len(entries) > 200 or sum(item.file_size for item in entries) > MAX_PACKAGE_BYTES:
                raise ValueError('Skill 最多 200 个文件，解压总大小不能超过 10 MB。')
            paths = [safe_path(item.filename) for item in entries]
            roots = [path for path in paths if PurePosixPath(path).name == 'SKILL.md']
            if len(roots) != 1:
                raise ValueError('压缩包必须包含唯一的 SKILL.md。')
            prefix = roots[0][:-len('SKILL.md')]
            files = {}
            for item, path in zip(entries, paths):
                if stat.S_ISLNK(item.external_attr >> 16) or not path.startswith(prefix):
                    raise ValueError('Skill 不允许符号链接或根目录以外的文件。')
                relative = safe_path(path[len(prefix):])
                if relative.casefold() in {name.casefold() for name in files}:
                    raise ValueError('Skill 包含重复文件路径。')
                files[relative] = archive.read(item)
            text = files.pop('SKILL.md').decode('utf-8-sig')
            return text, files
    except (zipfile.BadZipFile, UnicodeDecodeError, base64.binascii.Error) as exc:
        raise ValueError('Skill 压缩包不是有效的 ZIP/UTF-8 格式。') from exc


def available_skills(db: Session, member: WorkspaceMember) -> list[Skill]:
    query = db.query(Skill).filter(Skill.workspace_id == member.workspace_id)
    if not can_manage(member.role):
        query = query.filter(or_(Skill.is_listed.is_(True), Skill.created_by == member.user_id))
    return query.order_by(Skill.updated_at.desc()).all()


def accessible_skill(db: Session, member: WorkspaceMember, skill_id: int) -> Skill:
    skill = next((item for item in available_skills(db, member) if item.id == skill_id), None)
    if not skill:
        raise ValueError('Skill 不存在或没有访问权限。')
    return skill


def skill_payload(db: Session, skill: Skill, member: WorkspaceMember) -> dict:
    versions = db.query(SkillVersion).filter(SkillVersion.skill_id == skill.id).order_by(SkillVersion.version.desc()).all()
    return {'id': skill.id, 'slug': skill.slug, 'name': skill.name, 'description': skill.description, 'category': skill.category,
            'enabled': skill.enabled, 'is_listed': skill.is_listed, 'created_by': skill.created_by,
            'current_version_id': skill.current_version_id, 'can_edit': can_manage(member.role) or skill.created_by == member.user_id,
            'versions': [{'id': item.id, 'version': item.version, 'digest': item.digest, 'scripts_approved': item.scripts_approved,
                          'metadata': item.metadata_json} for item in versions]}


def save_skill(db: Session, member: WorkspaceMember, payload: dict, skill: Skill | None = None) -> Skill:
    if skill and not (can_manage(member.role) or skill.created_by == member.user_id):
        raise ValueError('没有编辑此 Skill 的权限。')
    if not skill and db.query(Skill.id).filter(Skill.workspace_id == member.workspace_id).count() >= 200:
        raise ValueError('当前工作区最多保存 200 个 Skill。')
    if skill:
        skill = db.query(Skill).filter(Skill.id == skill.id).with_for_update().populate_existing().first()
    previous = db.query(SkillVersion).filter(SkillVersion.id == skill.current_version_id).with_for_update().first() if skill else None
    files = None
    text = payload.get('instructions')
    if payload.get('package_base64'):
        text, files = unpack_package(payload['package_base64'])
    if not skill or text is not None or 'scripts_approved' in payload:
        if text is None and previous:
            text = '---\n' + yaml.safe_dump(previous.metadata_json, allow_unicode=True) + '---\n' + previous.instructions
        metadata, instructions = parse_instructions(text or '')
        if skill and metadata['name'] != skill.slug:
            raise ValueError('新版本需保持同一个 Skill name。')
        if payload.get('scripts_approved') and not can_manage(member.role):
            raise ValueError('只有管理员可以批准 Skill 脚本执行。')
        if not skill:
            existing = db.query(Skill.id).filter(Skill.workspace_id == member.workspace_id, Skill.created_by == member.user_id, Skill.slug == metadata['name']).first()
            if existing:
                raise ValueError('同名 Skill 已存在，请编辑并创建新版本。')
            skill = Skill(workspace_id=member.workspace_id, created_by=member.user_id, slug=metadata['name'], name=payload.get('name') or metadata['name'])
            db.add(skill)
            db.flush()
        # Lock the skill while allocating a monotonically increasing version number.
        db.query(Skill).filter(Skill.id == skill.id).with_for_update().first()
        versions = db.query(SkillVersion).filter(SkillVersion.skill_id == skill.id).with_for_update().all()
        if len(versions) >= 50:
            raise ValueError('单个 Skill 最多保存 50 个版本。')
        if files is None:
            files = {item.path: base64.b64decode(item.content_base64) for item in db.query(SkillFile).filter(SkillFile.version_id == previous.id).all()} if previous else {}
        digest = hashlib.sha256(json.dumps(metadata, sort_keys=True, ensure_ascii=False).encode() + instructions.encode())
        for path, data in sorted(files.items()):
            digest.update(path.encode() + data)
        version = SkillVersion(skill_id=skill.id, version=max((item.version for item in versions), default=0) + 1,
                               metadata_json=metadata, instructions=instructions, digest=digest.hexdigest(),
                               scripts_approved=bool(payload.get('scripts_approved', False)))
        db.add(version)
        db.flush()
        for path, data in files.items():
            db.add(SkillFile(version_id=version.id, path=path, content_base64=base64.b64encode(data).decode(), size=len(data), digest=hashlib.sha256(data).hexdigest()))
        skill.current_version_id = version.id
        skill.description = metadata['description']
    for key in ('name', 'category', 'enabled', 'is_listed'):
        if key in payload and payload[key] is not None:
            setattr(skill, key, payload[key])
    db.commit()
    db.refresh(skill)
    return skill


def skill_bindings(db: Session, agent_id: int) -> list[dict]:
    return [{'skill_id': item.skill_id, 'version_id': item.version_id, 'enabled': item.enabled}
            for item in db.query(AgentSkillBinding).filter(AgentSkillBinding.agent_id == agent_id).all()]


def replace_skill_bindings(db: Session, member: WorkspaceMember, agent_id: int, items: list[dict]) -> None:
    if len(items) > 20 or len({item['skill_id'] for item in items}) != len(items):
        raise ValueError('最多绑定 20 个不重复的 Skill。')
    validated = []
    for item in items:
        skill = accessible_skill(db, member, item['skill_id'])
        version = db.get(SkillVersion, item['version_id'])
        if not skill.enabled or not version or version.skill_id != skill.id:
            raise ValueError('Skill 或版本不可用。')
        validated.append(AgentSkillBinding(agent_id=agent_id, skill_id=skill.id, version_id=version.id, enabled=item.get('enabled', True)))
    db.query(AgentSkillBinding).filter(AgentSkillBinding.agent_id == agent_id).delete(synchronize_session=False)
    db.add_all(validated)
    db.commit()


def delete_skill(db: Session, member: WorkspaceMember, skill: Skill) -> None:
    if not (can_manage(member.role) or skill.created_by == member.user_id):
        raise ValueError('没有移除此 Skill 的权限。')
    if db.query(AgentSkillBinding.id).filter(AgentSkillBinding.skill_id == skill.id).first():
        raise ValueError('Skill 已绑定智能体，请先解除绑定。')
    published = db.query(AgentVersion).join(Agent, Agent.published_version_id == AgentVersion.id).filter(Agent.workspace_id == member.workspace_id).all()
    if any(item.get('skill_id') == skill.id for version in published for item in (version.snapshot or {}).get('skill_bindings') or []):
        raise ValueError('已发布智能体仍引用此 Skill，请先更新发布版本。')
    version_ids = [item.id for item in db.query(SkillVersion).filter(SkillVersion.skill_id == skill.id).all()]
    db.query(SkillFile).filter(SkillFile.version_id.in_(version_ids)).delete(synchronize_session=False)
    db.query(SkillVersion).filter(SkillVersion.skill_id == skill.id).delete(synchronize_session=False)
    db.delete(skill)
    db.commit()
