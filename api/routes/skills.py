"""Workspace Skill packages, versions, and Agent bindings."""
import base64
import yaml

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.access import require_workspace_agent, require_agent_write_access
from api.deps import get_current_membership
from core.db.models import WorkspaceMember, SkillVersion, SkillFile
from core.db.session import get_db
from core.services import skills

router = APIRouter(tags=['skills'])


class SkillSaveRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, max_length=80)
    instructions: str | None = Field(default=None, max_length=49152)
    package_base64: str | None = Field(default=None, max_length=14000000)
    enabled: bool | None = None
    is_listed: bool | None = None
    scripts_approved: bool | None = None


class SkillBindingItem(BaseModel):
    skill_id: int = Field(gt=0)
    version_id: int = Field(gt=0)
    enabled: bool = True


class SkillBindingsRequest(BaseModel):
    items: list[SkillBindingItem] = Field(max_length=20)


@router.get('/api/skills')
def list_skills(member: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    return {'items': [skills.skill_payload(db, item, member) for item in skills.available_skills(db, member)]}


@router.post('/api/skills')
def create_skill(request: SkillSaveRequest, member: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    try:
        skill = skills.save_skill(db, member, request.model_dump(exclude_unset=True))
        return {'skill': skills.skill_payload(db, skill, member)}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.patch('/api/skills/{skill_id}')
def update_skill(skill_id: int, request: SkillSaveRequest, member: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    try:
        skill = skills.accessible_skill(db, member, skill_id)
        skill = skills.save_skill(db, member, request.model_dump(exclude_unset=True), skill)
        return {'skill': skills.skill_payload(db, skill, member)}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.get('/api/skills/{skill_id}/versions/{version_id}')
def get_skill_version(skill_id: int, version_id: int, member: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    try:
        skills.accessible_skill(db, member, skill_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    version = db.get(SkillVersion, version_id)
    if not version or version.skill_id != skill_id:
        raise HTTPException(404, 'Skill 版本不存在。')
    files = db.query(SkillFile).filter(SkillFile.version_id == version.id).all()
    return {'version': {'id': version.id, 'version': version.version, 'metadata': version.metadata_json,
                        'instructions': version.instructions, 'scripts_approved': version.scripts_approved,
                        'source': '---\n' + yaml.safe_dump(version.metadata_json, allow_unicode=True) + '---\n' + version.instructions,
                        'files': [{'path': item.path, 'size': item.size} for item in files]}}


@router.get('/api/skills/{skill_id}/versions/{version_id}/files/{path:path}')
def read_skill_file(skill_id: int, version_id: int, path: str, member: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    try:
        skills.accessible_skill(db, member, skill_id)
        path = skills.safe_path(path)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    version = db.get(SkillVersion, version_id)
    if not version or version.skill_id != skill_id:
        raise HTTPException(404, 'Skill 版本不存在。')
    file = db.query(SkillFile).filter(SkillFile.version_id == version_id, SkillFile.path == path).first()
    if not file:
        raise HTTPException(404, 'Skill 文件不存在。')
    raw = base64.b64decode(file.content_base64)
    return {'path': path, 'size': file.size, 'content_base64': file.content_base64,
            'text': raw.decode('utf-8', errors='replace')[:20000]}


@router.delete('/api/skills/{skill_id}')
def remove_skill(skill_id: int, member: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    try:
        skills.delete_skill(db, member, skills.accessible_skill(db, member, skill_id))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'deleted': True}


@router.get('/api/agents/{agent_id}/skill-bindings')
def get_skill_bindings(agent_id: int, member: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    require_agent_write_access(require_workspace_agent(db, member.workspace_id, agent_id), member)
    return {'items': skills.skill_bindings(db, agent_id)}


@router.put('/api/agents/{agent_id}/skill-bindings')
def put_skill_bindings(agent_id: int, request: SkillBindingsRequest, member: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    require_agent_write_access(require_workspace_agent(db, member.workspace_id, agent_id), member)
    try:
        skills.replace_skill_bindings(db, member, agent_id, [item.model_dump() for item in request.items])
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    return {'items': skills.skill_bindings(db, agent_id)}
