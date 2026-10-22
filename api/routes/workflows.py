"""灵枢 Agent 平台 —— 独立工作流资源路由。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.deps import get_current_membership
from api.schemas import WorkflowCreateRequest, WorkflowUpdateRequest, WorkflowTestRequest
from core.db.models import Agent, Session as ChatSession, WorkspaceMember
from core.db.session import get_db
from core.runtime.graph import validate_graph
from core.runtime.workflow import WorkflowRunner
from core.services.workflows import (
    create_workflow,
    delete_workflow,
    get_workspace_workflow,
    list_workspace_workflows,
    update_workflow,
    workflow_payload,
)

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


def _require_workflow(db: Session, workspace_id: int, workflow_id: int):
    try:
        return get_workspace_workflow(db, workspace_id, workflow_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("")
def list_workflows(membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    return {"items": [workflow_payload(item) for item in list_workspace_workflows(db, membership.workspace_id)]}


@router.post("")
def create_workflow_endpoint(request: WorkflowCreateRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    workflow = create_workflow(
        db,
        workspace_id=membership.workspace_id,
        user_id=membership.user_id,
        payload=request.model_dump(),
    )
    return {"workflow": workflow_payload(workflow), "nodes": workflow.nodes}


@router.get("/{workflow_id}")
def get_workflow_endpoint(workflow_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    workflow = _require_workflow(db, membership.workspace_id, workflow_id)
    return {"workflow": workflow_payload(workflow), "nodes": workflow.nodes}


@router.patch("/{workflow_id}")
def update_workflow_endpoint(workflow_id: int, request: WorkflowUpdateRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    workflow = _require_workflow(db, membership.workspace_id, workflow_id)
    payload = request.model_dump(exclude_unset=True)
    if payload.get("nodes") is not None:
        errors = validate_graph(payload["nodes"])
        if errors:
            raise HTTPException(status_code=400, detail="; ".join(errors))
    workflow = update_workflow(db, workflow, payload)
    return {"workflow": workflow_payload(workflow), "nodes": workflow.nodes}


@router.delete("/{workflow_id}")
def delete_workflow_endpoint(workflow_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    workflow = _require_workflow(db, membership.workspace_id, workflow_id)
    delete_workflow(db, workflow)
    return {"deleted": True}


@router.post("/{workflow_id}/test")
def test_workflow_endpoint(workflow_id: int, request: WorkflowTestRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    """
    试运行：借一个智能体的配置（模型、知识库、工具）跑这条工作流。

    不落库成正式会话——用 `is_debug=True` 的临时会话，跑完随会话列表一起被过滤掉。
    """
    workflow = _require_workflow(db, membership.workspace_id, workflow_id)
    agent = db.query(Agent).filter(
        Agent.workspace_id == membership.workspace_id,
        Agent.id == request.agent_id,
    ).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    errors = validate_graph(workflow.nodes or [])
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    session = ChatSession(
        workspace_id=membership.workspace_id,
        agent_id=agent.id,
        user_id=membership.user_id,
        title="工作流试运行",
        is_debug=True,
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    try:
        run, answer, sources, steps = WorkflowRunner(db).run(
            agent=agent,
            chat_session=session,
            user_message=request.message,
            mode="draft",
            variables=request.variables,
            workflow_override=workflow.nodes,
        )
    except (ValueError, RuntimeError) as exc:
        # 试运行就是为了看清哪里失败：模型调用失败（RuntimeError）也把原因带回面板
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "run_id": run.id,
        "status": run.status,
        "answer": answer,
        "sources": sources,
        "steps": [
            {"node_id": step["node_id"], "node_type": step["node_type"], "status": step["status"]}
            for step in steps
        ],
    }

