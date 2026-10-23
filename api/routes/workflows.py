"""灵枢 Agent 平台 —— 独立工作流资源路由。"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from api.deps import get_current_membership, get_current_user
from api.rate_limit import redis_rate_limit
from api.routes.chat import stream_chat_events
from api.schemas import ChatRequest, WorkflowCreateRequest, WorkflowUpdateRequest, WorkflowTestRequest
from core.db.models import Agent, Session as ChatSession, User, WorkspaceMember
from core.db.session import get_db
from core.runtime.graph import validate_graph
from core.runtime.workflow import WorkflowRunner
from core.services.workflows import (
    create_workflow,
    delete_workflow,
    ensure_workflow_agent,
    get_workspace_workflow,
    list_workspace_workflows,
    update_workflow,
    update_workflow_runtime,
    workflow_agents,
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
    agents = workflow_agents(db, membership.workspace_id)
    return {
        "items": [
            workflow_payload(item, agent=agents.get(item.id))
            for item in list_workspace_workflows(db, membership.workspace_id)
        ]
    }


@router.post("")
def create_workflow_endpoint(request: WorkflowCreateRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    workflow = create_workflow(
        db,
        workspace_id=membership.workspace_id,
        user_id=membership.user_id,
        payload=request.model_dump(),
    )
    agent = db.get(Agent, workflow.agent_id) if workflow.agent_id else None
    return {"workflow": workflow_payload(workflow, agent=agent), "nodes": workflow.nodes}


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
    runtime_keys = {"model_id", "user_model_config_id", "system_prompt", "temperature"}
    runtime_payload = {key: payload.pop(key) for key in list(payload) if key in runtime_keys}
    nodes_changed = payload.get("nodes") is not None
    workflow = update_workflow(db, workflow, payload)
    # 对话读的是宿主智能体的发布快照，所以节点或运行配置一变就要重发一版，
    # 否则用户改完流程去对话，跑的还是旧流程
    agent = ensure_workflow_agent(db, workflow, user_id=membership.user_id)
    try:
        agent = update_workflow_runtime(db, workflow, runtime_payload, user_id=membership.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if nodes_changed and not runtime_payload:
        from core.services.agents import publish_agent

        publish_agent(db, agent, membership.user_id)
    return {"workflow": workflow_payload(workflow, agent=agent), "nodes": workflow.nodes}


@router.delete("/{workflow_id}")
def delete_workflow_endpoint(workflow_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    workflow = _require_workflow(db, membership.workspace_id, workflow_id)
    delete_workflow(db, workflow)
    return {"deleted": True}


@router.post("/{workflow_id}/chat/stream", dependencies=[Depends(redis_rate_limit(30, 60, scope="workflow-chat"))])
def workflow_chat_stream(workflow_id: int, request: ChatRequest, background_tasks: BackgroundTasks, membership: WorkspaceMember = Depends(get_current_membership), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """直接和工作流对话：会话与运行记在它的宿主智能体名下，跑的是工作流自己的节点。"""
    workflow = _require_workflow(db, membership.workspace_id, workflow_id)
    agent = ensure_workflow_agent(db, workflow, user_id=membership.user_id)
    return StreamingResponse(
        stream_chat_events(db, agent, current_user.id, request, background_tasks),
        media_type="text/event-stream",
    )


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

