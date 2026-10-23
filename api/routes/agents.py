"""灵枢 Agent 平台 —— 智能体路由（含 Workflow + Memory Profile）。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.access import (
    require_agent_read_access,
    require_agent_write_access,
    require_workspace_agent,
)
from api.deps import get_current_membership
from api.schemas import (
    AgentCreateRequest,
    AgentSubAgentBindingsRequest,
    AgentUpdateRequest,
    MemoryProfileUpdateRequest,
    WorkflowUpdateRequest,
)
from core.db.models import Agent, AgentVersion, Workflow, WorkflowDefinition, WorkspaceMember
from core.db.session import get_db
from core.runtime.graph import validate_graph
from core.runtime.workflow import default_workflow
from core.security.permissions import can_manage
from core.services.agents import (
    agent_sub_agent_bindings,
    agent_summary,
    create_agent,
    ensure_template_agents_published,
    get_agent_detail,
    publish_agent,
    replace_agent_sub_agent_bindings,
    update_agent,
)
from core.services.agents import (
    delete_agent as delete_agent_service,
)
from core.services.memory import (
    delete_memory_profile,
    get_memory_profile,
    memory_profile_payload,
    upsert_memory_profile,
)
from core.services.tools import validate_tool_ids

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _apply_model_selection(db: Session, payload: dict, *, user_id: int) -> dict:
    """智能体模型解析（从 main.py 提取）"""
    from core.db.models import ModelConfig, UserModelConfig
    if payload.get("user_model_config_id"):
        config = db.query(UserModelConfig).filter(
            UserModelConfig.id == payload["user_model_config_id"],
            UserModelConfig.user_id == user_id,
            UserModelConfig.enabled.is_(True),
        ).first()
        if not config:
            raise HTTPException(status_code=400, detail="User model config is not available")
        payload["model_id"] = None
        payload["model"] = config.chat_model
        if payload.get("temperature") is None:
            payload["temperature"] = config.default_temperature
        return payload
    if not payload.get("model_id") and not payload.get("user_model_config_id") and "model_id" in payload and "user_model_config_id" in payload:
        default_user_model = db.query(UserModelConfig).filter(
            UserModelConfig.user_id == user_id, UserModelConfig.enabled.is_(True), UserModelConfig.is_default.is_(True),
        ).order_by(UserModelConfig.id.asc()).first()
        if default_user_model:
            payload["user_model_config_id"] = default_user_model.id
            payload["model_id"] = None
            payload["model"] = default_user_model.chat_model
            if payload.get("temperature") is None:
                payload["temperature"] = default_user_model.default_temperature
            return payload
        payload["model"] = None
    if payload.get("model_id"):
        payload["user_model_config_id"] = None
    if not payload.get("model_id"):
        return payload
    model = db.get(ModelConfig, payload["model_id"])
    if not model or not model.enabled:
        raise HTTPException(status_code=400, detail="Model is not available")
    payload["model"] = model.model_name
    if payload.get("temperature") is None:
        payload["temperature"] = model.default_temperature
    return payload


def _validate_workflow_nodes(nodes: list[dict]) -> None:
    errors = validate_graph(nodes)
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))


@router.get("")
def list_agents(membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    ensure_template_agents_published(db, membership.workspace_id)
    query = db.query(Agent).filter(Agent.workspace_id == membership.workspace_id)
    if not can_manage(membership.role):
        query = query.filter(Agent.created_by == membership.user_id)
    agents = query.order_by(Agent.updated_at.desc()).all()
    # 工作流的宿主智能体只是运行载体：标出来，前端在智能体列表里过滤，对话页仍可用它发起对话
    host_ids = {
        row.agent_id
        for row in db.query(Workflow.agent_id).filter(Workflow.workspace_id == membership.workspace_id).all()
        if row.agent_id
    }
    items = []
    for agent in agents:
        summary = agent_summary(agent)
        summary["is_workflow_host"] = agent.id in host_ids
        items.append(summary)
    return {"items": items}


@router.post("")
def create_agent_endpoint(request: AgentCreateRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    try:
        validate_tool_ids(db, workspace_id=membership.workspace_id, user_id=membership.user_id, tool_ids=request.tool_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = _apply_model_selection(db, request.model_dump(), user_id=membership.user_id)
    agent = create_agent(db, workspace_id=membership.workspace_id, user_id=membership.user_id, payload=payload)
    return {"agent": get_agent_detail(db, agent)}


@router.get("/{agent_id}")
def get_agent(agent_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    agent = require_workspace_agent(db, membership.workspace_id, agent_id)
    require_agent_read_access(agent, membership)
    return {"agent": get_agent_detail(db, agent)}


@router.patch("/{agent_id}")
def patch_agent(agent_id: int, request: AgentUpdateRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    agent = require_workspace_agent(db, membership.workspace_id, agent_id)
    require_agent_write_access(agent, membership)
    if request.tool_ids is not None:
        try:
            validate_tool_ids(db, workspace_id=membership.workspace_id, user_id=membership.user_id, tool_ids=request.tool_ids)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    agent = update_agent(db, agent, _apply_model_selection(db, request.model_dump(exclude_unset=True), user_id=membership.user_id))
    return {"agent": get_agent_detail(db, agent)}


@router.get("/{agent_id}/memory-profile")
def get_agent_memory_profile(agent_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    agent = require_workspace_agent(db, membership.workspace_id, agent_id)
    require_agent_read_access(agent, membership)
    profile = get_memory_profile(db, workspace_id=membership.workspace_id, user_id=membership.user_id, agent_id=agent.id)
    return {"profile": memory_profile_payload(profile, agent_id=agent.id)}


@router.patch("/{agent_id}/memory-profile")
def patch_agent_memory_profile(agent_id: int, request: MemoryProfileUpdateRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    agent = require_workspace_agent(db, membership.workspace_id, agent_id)
    require_agent_read_access(agent, membership)
    try:
        profile = upsert_memory_profile(db, workspace_id=membership.workspace_id, user_id=membership.user_id, agent_id=agent.id, payload=request.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"profile": memory_profile_payload(profile)}


@router.delete("/{agent_id}/memory-profile")
def delete_agent_memory_profile(agent_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    agent = require_workspace_agent(db, membership.workspace_id, agent_id)
    require_agent_read_access(agent, membership)
    delete_memory_profile(db, workspace_id=membership.workspace_id, user_id=membership.user_id, agent_id=agent.id)
    return {"deleted": True}


@router.delete("/{agent_id}")
def delete_agent_endpoint(agent_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    agent = require_workspace_agent(db, membership.workspace_id, agent_id)
    require_agent_write_access(agent, membership)
    try:
        delete_agent_service(db, agent)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"deleted": True}


@router.post("/{agent_id}/publish")
def publish_agent_endpoint(agent_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    agent = require_workspace_agent(db, membership.workspace_id, agent_id)
    require_agent_write_access(agent, membership)
    version = publish_agent(db, agent, membership.user_id)
    return {"status": agent.status, "review_required": False, "version": {"id": version.id, "version": version.version, "snapshot": version.snapshot}}


@router.get("/{agent_id}/versions")
def list_versions(agent_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    agent = require_workspace_agent(db, membership.workspace_id, agent_id)
    require_agent_read_access(agent, membership)
    versions = db.query(AgentVersion).filter_by(agent_id=agent.id).order_by(AgentVersion.version.desc()).all()
    return {"items": [{"id": item.id, "version": item.version, "created_at": item.created_at.isoformat()} for item in versions]}


@router.get("/{agent_id}/draft")
def get_draft(agent_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    agent = require_workspace_agent(db, membership.workspace_id, agent_id)
    require_agent_write_access(agent, membership)
    return {"agent": get_agent_detail(db, agent)}


@router.get("/{agent_id}/workflow")
def get_workflow(agent_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    agent = require_workspace_agent(db, membership.workspace_id, agent_id)
    require_agent_read_access(agent, membership)
    workflow = db.query(WorkflowDefinition).filter(WorkflowDefinition.agent_id == agent.id).first()
    return {"nodes": workflow.nodes if workflow else default_workflow()}


@router.patch("/{agent_id}/workflow")
def update_workflow(agent_id: int, request: WorkflowUpdateRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    agent = require_workspace_agent(db, membership.workspace_id, agent_id)
    require_agent_write_access(agent, membership)
    _validate_workflow_nodes(request.nodes)
    workflow = db.query(WorkflowDefinition).filter(WorkflowDefinition.agent_id == agent.id).first()
    if not workflow:
        workflow = WorkflowDefinition(agent_id=agent.id, nodes=request.nodes)
        db.add(workflow)
    else:
        workflow.nodes = request.nodes
    db.commit()
    return {"nodes": workflow.nodes}


@router.get("/{agent_id}/sub-agent-bindings")
def get_sub_agent_bindings(agent_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    """读取当前智能体可作为工具调用的子智能体列表。"""
    agent = require_workspace_agent(db, membership.workspace_id, agent_id)
    require_agent_read_access(agent, membership)
    return {"items": agent_sub_agent_bindings(db, agent.id)}


@router.put("/{agent_id}/sub-agent-bindings")
def put_sub_agent_bindings(agent_id: int, request: AgentSubAgentBindingsRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    """全量覆盖子智能体绑定：绑定后模型可在对话中按需委派任务给它们。"""
    agent = require_workspace_agent(db, membership.workspace_id, agent_id)
    require_agent_write_access(agent, membership)
    try:
        replace_agent_sub_agent_bindings(
            db,
            agent,
            [item.model_dump() for item in request.items],
            workspace_id=membership.workspace_id,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": agent_sub_agent_bindings(db, agent.id)}
