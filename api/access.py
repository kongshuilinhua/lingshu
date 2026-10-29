"""
灵枢 Agent 平台 —— API 访问控制辅助函数。

🎯 架构角色：
    从 api/main.py 中提取权限检查和资源归属校验函数。
    分层设计：Controller 层只做路由调度，权限验证由独立模块提供。
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core.db.models import Agent, KnowledgeBase, Session as ChatSession, WorkspaceMember
from core.security.permissions import can_manage


def require_workspace_agent(db: Session, workspace_id: int, agent_id: int) -> Agent:
    agent = db.query(Agent).filter(Agent.workspace_id == workspace_id, Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


def require_agent_read_access(agent: Agent, membership: WorkspaceMember) -> None:
    if can_manage(membership.role):
        return
    if agent.created_by == membership.user_id:
        return
    raise HTTPException(status_code=403, detail="Agent access denied")


def require_agent_write_access(agent: Agent, membership: WorkspaceMember) -> None:
    if can_manage(membership.role):
        return
    if agent.created_by == membership.user_id:
        return
    raise HTTPException(status_code=403, detail="Agent edit denied")


def require_session_access(session: ChatSession, membership: WorkspaceMember) -> None:
    if can_manage(membership.role):
        return
    if session.user_id == membership.user_id:
        return
    raise HTTPException(status_code=404, detail="Session not found")


def require_workspace_kb(db: Session, workspace_id: int, kb_id: int) -> KnowledgeBase:
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.workspace_id == workspace_id, KnowledgeBase.id == kb_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    return kb


def require_kb_write_access(kb: KnowledgeBase, membership: WorkspaceMember) -> None:
    if can_manage(membership.role):
        return
    if kb.created_by == membership.user_id:
        return
    raise HTTPException(status_code=403, detail="Knowledge base edit denied")


def require_workflow_write_access(workflow, membership: WorkspaceMember) -> None:
    """
    工作流写权限：创建者本人，或管理员。

    🎯 为什么列表开放、写操作收窄：工作流是工作区共享资源，成员之间要能互相看到、互相跑；
        但改和删会连带删掉宿主智能体（`core/services/workflows.py` 的 delete_workflow），
        别人的流程不该被任何人随手改掉。这个语义与触发器完全一致。
    """
    if can_manage(membership.role):
        return
    if workflow.created_by == membership.user_id:
        return
    raise HTTPException(status_code=403, detail="Workflow edit denied")


def invite_workspace(db: Session, workspace_id: int):
    from core.db.models import Workspace
    workspace = db.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return workspace
