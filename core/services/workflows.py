"""灵枢 Agent 平台 —— 独立工作流资源服务。

工作流从这个版本起是一等资源：有自己的名字、归属工作区，可以被智能体引用，
也可以在「工作流」入口里独立编辑和试运行。

与 `WorkflowDefinition`（智能体内置流程）的区别：
    内置流程是智能体的一个配置字段，随智能体发布快照固化；
    独立工作流是可以被多个智能体共用的编排单元。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.db.models import Agent, Workflow
from core.services.bootstrap import DEFAULT_WORKFLOW


def workflow_payload(workflow: Workflow, *, agent: Agent | None = None, node_count: int | None = None) -> dict:
    """`agent_id` 是隐藏宿主智能体：前端拿它当作对话目标，同时用来展示模型配置。"""
    return {
        "id": workflow.id,
        "name": workflow.name,
        "description": workflow.description or "",
        "node_count": node_count if node_count is not None else len(workflow.nodes or []),
        "agent_id": workflow.agent_id,
        "model": agent.model if agent else None,
        "user_model_config_id": agent.user_model_config_id if agent else None,
        "system_prompt": agent.system_prompt if agent else "",
        "temperature": agent.temperature if agent else None,
        # 前端按它判断「这条流程我能不能改」——不看归属就只能靠角色，
        # 会把按钮给到不该有的成员，点下去必 403
        "created_by": workflow.created_by,
        "created_at": workflow.created_at.isoformat() if workflow.created_at else None,
        "updated_at": workflow.updated_at.isoformat() if workflow.updated_at else None,
    }


def ensure_workflow_agent(db: Session, workflow: Workflow, *, user_id: int) -> Agent:
    """
    取得（必要时创建）工作流的宿主智能体。

    🎯 为什么要一个智能体：`sessions.agent_id` 与 `runs.agent_id` 是非空外键，
    借用既有智能体承接会话，整套对话、记忆、运行轨迹链路都不用另起一套。
    它不出现在智能体列表与市场里，用户看到的是工作流。
    """
    if workflow.agent_id:
        agent = db.get(Agent, workflow.agent_id)
        if agent:
            return agent
    from core.services.agents import create_agent

    agent = create_agent(
        db,
        workspace_id=workflow.workspace_id,
        user_id=user_id,
        payload={
            "name": workflow.name,
            "avatar": "WF",
            "description": f"工作流「{workflow.name}」的运行配置",
            "system_prompt": "",
            "workflow_id": workflow.id,
            # 不显式指定模型：由 create_agent 统一补默认模型，
            # 否则首次「去对话」用的是没有凭证的默认名，必然 401
        },
    )
    workflow.agent_id = agent.id
    db.commit()
    db.refresh(workflow)
    from core.services.agents import publish_agent

    publish_agent(db, agent, user_id)
    return agent


def update_workflow_runtime(db: Session, workflow: Workflow, payload: dict, *, user_id: int) -> Agent:
    """改工作流的模型/提示词：写进宿主智能体，并立刻发布一版，对话读到的就是最新配置。"""
    from core.services.agents import publish_agent, update_agent

    agent = ensure_workflow_agent(db, workflow, user_id=user_id)
    agent = update_agent(db, agent, {**payload, "workflow_id": workflow.id})
    publish_agent(db, agent, user_id)
    return agent


def get_workspace_workflow(db: Session, workspace_id: int, workflow_id: int) -> Workflow:
    workflow = db.query(Workflow).filter(
        Workflow.workspace_id == workspace_id,
        Workflow.id == workflow_id,
    ).first()
    if not workflow:
        raise ValueError("Workflow not found")
    return workflow


def list_workspace_workflows(db: Session, workspace_id: int) -> list[Workflow]:
    return (
        db.query(Workflow)
        .filter(Workflow.workspace_id == workspace_id)
        .order_by(Workflow.updated_at.desc())
        .all()
    )


def workflow_agents(db: Session, workspace_id: int) -> dict[int, Agent]:
    """工作流 id → 宿主智能体，用于列表里展示模型配置。"""
    workflows = list_workspace_workflows(db, workspace_id)
    ids = [item.agent_id for item in workflows if item.agent_id]
    agents = db.query(Agent).filter(Agent.id.in_(ids)).all() if ids else []
    by_id = {agent.id: agent for agent in agents}
    return {item.id: by_id[item.agent_id] for item in workflows if item.agent_id in by_id}


def create_workflow(db: Session, *, workspace_id: int, user_id: int, payload: dict) -> Workflow:
    workflow = Workflow(
        workspace_id=workspace_id,
        created_by=user_id,
        name=(payload.get("name") or "未命名工作流").strip()[:160],
        description=payload.get("description") or "",
        nodes=payload.get("nodes") or DEFAULT_WORKFLOW,
    )
    db.add(workflow)
    db.commit()
    db.refresh(workflow)
    ensure_workflow_agent(db, workflow, user_id=user_id)
    return workflow


def update_workflow(db: Session, workflow: Workflow, payload: dict) -> Workflow:
    for key in ("name", "description"):
        if payload.get(key) is not None:
            setattr(workflow, key, payload[key])
    if payload.get("nodes") is not None:
        workflow.nodes = payload["nodes"]
    db.commit()
    db.refresh(workflow)
    return workflow


def delete_workflow(db: Session, workflow: Workflow) -> None:
    # 引用方退回内置流程。不能只靠外键 ON DELETE SET NULL：老库的 workflow_id
    # 是兼容迁移 ALTER 加的列，没有外键约束，不清空就会留下悬空引用
    db.query(Agent).filter(Agent.workflow_id == workflow.id).update(
        {Agent.workflow_id: None}, synchronize_session=False
    )
    host_id = workflow.agent_id
    workflow.agent_id = None
    db.delete(workflow)
    db.commit()
    if host_id:
        from core.services.agents import delete_agent

        host = db.get(Agent, host_id)
        if host:
            delete_agent(db, host)
