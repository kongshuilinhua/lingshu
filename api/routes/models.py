"""灵枢 Agent 平台 —— 模型配置路由。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.deps import get_current_membership, get_current_user, require_manager
from api.schemas import (
    ModelConfigRequest,
    ModelConfigUpdateRequest,
    UserModelCapabilityTestRequest,
    UserModelConfigRequest,
    UserModelConfigUpdateRequest,
    UserModelProbeModelsRequest,
)
from core.db.models import ModelConfig, User, WorkspaceMember
from core.db.session import get_db
from core.security.permissions import can_manage
from core.services.bootstrap import ensure_default_models
from core.services.models import (
    create_model_config,
    delete_model_config,
    model_payload,
    update_model_config,
)
from core.services.user_models import (
    create_user_model_config,
    delete_user_model_config,
    get_owned_user_model,
    list_user_model_configs,
    probe_models_payload,
    test_user_model_config,
    test_user_model_payload,
    update_user_model_config,
    user_model_payload,
)

router = APIRouter(tags=["models"])


# ── 系统全局模型 ──

@router.get("/api/models")
def list_models(
    include_disabled: bool = Query(default=False),
    membership: WorkspaceMember = Depends(get_current_membership),
    db: Session = Depends(get_db),
):
    ensure_default_models(db)
    query = db.query(ModelConfig)
    if include_disabled:
        if not can_manage(membership.role):
            raise HTTPException(status_code=403, detail="Admin role required")
    else:
        query = query.filter(ModelConfig.enabled.is_(True))
    models = query.order_by(ModelConfig.id.asc()).all()
    return {"items": [model_payload(model) for model in models]}


@router.post("/api/admin/models")
def create_model(request: ModelConfigRequest, _: WorkspaceMember = Depends(require_manager), db: Session = Depends(get_db)):
    if db.query(ModelConfig).filter(ModelConfig.model_name == request.model_name).first():
        raise HTTPException(status_code=409, detail="Model already exists")
    model = create_model_config(db, request.model_dump())
    return {"model": model_payload(model)}


@router.patch("/api/admin/models/{model_id}")
def patch_model(model_id: int, request: ModelConfigUpdateRequest, _: WorkspaceMember = Depends(require_manager), db: Session = Depends(get_db)):
    model = db.get(ModelConfig, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    payload = request.model_dump(exclude_unset=True)
    if "model_name" in payload and payload["model_name"] != model.model_name:
        existing = db.query(ModelConfig).filter(ModelConfig.model_name == payload["model_name"]).first()
        if existing:
            raise HTTPException(status_code=409, detail="Model already exists")
    model = update_model_config(db, model, payload)
    return {"model": model_payload(model)}


@router.delete("/api/admin/models/{model_id}")
def delete_model(model_id: int, _: WorkspaceMember = Depends(require_manager), db: Session = Depends(get_db)):
    model = db.get(ModelConfig, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    try:
        delete_model_config(db, model)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"deleted": True}


# ── 用户私有模型 ──

@router.get("/api/user-models")
def list_user_models(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    configs = list_user_model_configs(db, user_id=current_user.id)
    return {"items": [user_model_payload(config) for config in configs]}


@router.post("/api/user-models")
def create_user_model(request: UserModelConfigRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        config = create_user_model_config(db, user_id=current_user.id, payload=request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"model_config": user_model_payload(config)}


@router.post("/api/user-models/test")
def test_user_model_draft(request: UserModelCapabilityTestRequest, current_user: User = Depends(get_current_user)):
    try:
        payload = request.model_dump()
        detect_image = bool(payload.pop("detect_image", False))
        return test_user_model_payload(payload, detect_image=detect_image)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/user-models/probe-models")
def probe_user_models(request: UserModelProbeModelsRequest, current_user: User = Depends(get_current_user)):
    """填 base_url + api_key 后拉取可用模型列表,前端下拉选 model,无需手记模型串。"""
    try:
        return probe_models_payload(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/api/user-models/{config_id}")
def patch_user_model(config_id: int, request: UserModelConfigUpdateRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    config = get_owned_user_model(db, user_id=current_user.id, config_id=config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Model config not found")
    try:
        config = update_user_model_config(db, config=config, payload=request.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"model_config": user_model_payload(config)}


@router.delete("/api/user-models/{config_id}")
def delete_user_model(config_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    config = get_owned_user_model(db, user_id=current_user.id, config_id=config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Model config not found")
    try:
        delete_user_model_config(db, config=config)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"deleted": True}


@router.post("/api/user-models/{config_id}/test")
def test_user_model(config_id: int, detect_image: bool = Query(default=True), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    config = get_owned_user_model(db, user_id=current_user.id, config_id=config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Model config not found")
    return test_user_model_config(config, detect_image=detect_image)
