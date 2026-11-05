"""灵枢 Agent 平台 —— 触发器路由。

管理端（需登录）：增删改查、立即试触发。
调用端（凭 token，无需登录）：外部系统 POST 到 hook 地址即唤起一次。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from api.deps import get_current_membership
from api.rate_limit import redis_rate_limit
from api.schemas import TriggerCreateRequest, TriggerUpdateRequest
from core.db.models import Trigger, WorkspaceMember
from core.db.session import get_db
from core.security.permissions import can_manage
from core.services.triggers import (
    create_trigger,
    find_by_token,
    list_workspace_triggers,
    run_trigger,
    trigger_payload,
    trigger_targets,
    update_trigger,
)

router = APIRouter(prefix="/api/triggers", tags=["triggers"])


def _base_url(request: Request) -> str:
    """外部系统拿到的调用地址必须是完整 URL，不能只给相对路径。"""
    return str(request.base_url).rstrip("/")


def _require_trigger(db: Session, membership: WorkspaceMember, trigger_id: int) -> Trigger:
    trigger = db.get(Trigger, trigger_id)
    if not trigger or trigger.workspace_id != membership.workspace_id:
        raise HTTPException(status_code=404, detail="Trigger not found")
    # 成员只能管自己建的触发器，管理员不限
    if not can_manage(membership.role) and trigger.created_by != membership.user_id:
        raise HTTPException(status_code=403, detail="Trigger access denied")
    return trigger


@router.get("")
def list_triggers(request: Request, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    items = list_workspace_triggers(db, membership.workspace_id)
    return {
        "items": [trigger_payload(item, base_url=_base_url(request)) for item in items],
        "targets": trigger_targets(db, membership.workspace_id),
    }


@router.post("")
def create_trigger_endpoint(request: TriggerCreateRequest, http_request: Request, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    try:
        trigger = create_trigger(db, workspace_id=membership.workspace_id, user_id=membership.user_id, payload=request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"trigger": trigger_payload(trigger, base_url=_base_url(http_request))}


@router.patch("/{trigger_id}")
def update_trigger_endpoint(trigger_id: int, request: TriggerUpdateRequest, http_request: Request, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    trigger = _require_trigger(db, membership, trigger_id)
    try:
        trigger = update_trigger(db, trigger, request.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"trigger": trigger_payload(trigger, base_url=_base_url(http_request))}


@router.delete("/{trigger_id}")
def delete_trigger_endpoint(trigger_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    trigger = _require_trigger(db, membership, trigger_id)
    db.delete(trigger)
    db.commit()
    return {"deleted": True}


@router.post("/{trigger_id}/run")
def run_trigger_endpoint(trigger_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    """立即跑一次，不用等定时或外部调用——配好触发器后先验证一遍。"""
    trigger = _require_trigger(db, membership, trigger_id)
    try:
        result = run_trigger(db, trigger, {"trigger_name": trigger.name, "now": "manual"})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/hook/{token}", dependencies=[Depends(redis_rate_limit(60, 60, scope="trigger-hook"))])
def trigger_hook(token: str, body: dict | None = None, db: Session = Depends(get_db)):
    """
    外部系统调用的入口：URL 里的 token 就是凭证，不再校验登录态。

    请求体里的字段可以直接填进消息模板：`{"order": "SO-1928"}` + 模板
    `订单 {{order}} 到了吗` → 用这条消息唤起目标。
    """
    trigger = find_by_token(db, token)
    if not trigger or not trigger.enabled:
        raise HTTPException(status_code=404, detail="Trigger not found or disabled")
    try:
        return run_trigger(db, trigger, body or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        # 模型/网关失败：把真实原因带回给调用方，否则外部系统只知道 500
        raise HTTPException(status_code=502, detail=str(exc)) from exc
