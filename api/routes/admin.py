"""灵枢 Agent 平台 —— Admin 路由（市场 + 成员管理）。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.access import require_workspace_agent
from api.deps import get_current_membership, get_current_user
from api.schemas import UploadCreateRequest
from core.db.models import (
    Agent,
    AgentVersion,
    User,
    Workflow,
    WorkspaceMember,
)
from core.db.session import get_db
from core.services.agents import (
    get_agent_detail,
    market_agent_summary,
)
from core.services.uploads import create_upload, upload_payload
from core.services.agents import copy_agent_from_market

router = APIRouter(tags=["admin"])


# ── Market ──

@router.get("/api/market/agents")
def list_market_agents(membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    host_ids = {
        row.agent_id
        for row in db.query(Workflow.agent_id).filter(Workflow.workspace_id == membership.workspace_id).all()
        if row.agent_id
    }
    agents = db.query(Agent).filter(
        Agent.workspace_id == membership.workspace_id, Agent.status == "published", Agent.published_version_id.isnot(None),
    ).order_by(Agent.updated_at.desc()).all()
    # 工作流的宿主智能体只是运行载体，不进市场
    agents = [agent for agent in agents if agent.id not in host_ids]
    items = []
    for agent in agents:
        version = db.get(AgentVersion, agent.published_version_id) if agent.published_version_id else None
        items.append(market_agent_summary(agent, version))
    return {"items": items}


@router.post("/api/market/agents/{agent_id}/copy")
def copy_market_agent(agent_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    source = require_workspace_agent(db, membership.workspace_id, agent_id)
    if source.status != "published" or not source.published_version_id:
        raise HTTPException(status_code=404, detail="Market agent not found")
    try:
        copied = copy_agent_from_market(db, source=source, user_id=membership.user_id, workspace_id=membership.workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"agent": get_agent_detail(db, copied)}


# ── Uploads ──

@router.post("/api/uploads")
def upload_file(request: UploadCreateRequest, membership: WorkspaceMember = Depends(get_current_membership), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        upload = create_upload(db, workspace_id=membership.workspace_id, user_id=current_user.id, filename=request.filename, content_type=request.content_type, content_base64=request.content_base64)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"upload": upload_payload(upload)}
