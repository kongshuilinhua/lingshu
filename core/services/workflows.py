"""灵枢 Agent 平台 —— 独立工作流资源服务。

工作流从这个版本起是一等资源：有自己的名字、归属工作区，可以被智能体引用，
也可以在「工作流」入口里独立编辑和试运行。

与 `WorkflowDefinition`（智能体内置流程）的区别：
    内置流程是智能体的一个配置字段，随智能体发布快照固化；
    独立工作流是可以被多个智能体共用的编排单元。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.db.models import Workflow
from core.services.bootstrap import DEFAULT_WORKFLOW


def workflow_payload(workflow: Workflow, *, node_count: int | None = None) -> dict:
    return {
        "id": workflow.id,
        "name": workflow.name,
        "description": workflow.description or "",
        "node_count": node_count if node_count is not None else len(workflow.nodes or []),
        "created_at": workflow.created_at.isoformat() if workflow.created_at else None,
        "updated_at": workflow.updated_at.isoformat() if workflow.updated_at else None,
    }


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
    db.delete(workflow)
    db.commit()
