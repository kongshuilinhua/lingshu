from __future__ import annotations

import httpx
import re
import time

from sqlalchemy.orm import Session

from core.config import get_settings
from core.db.models import Agent, UserModelConfig
from core.integrations.llm import OpenAICompatibleProvider
from core.security.api_keys import decrypt_api_key, encrypt_api_key


def user_model_payload(config: UserModelConfig) -> dict:
    """
    将用户私有模型（BYOK）实体序列化为安全的 API DTO。
    
    🛡️ 脱敏过滤：
        使用has_api_key判定密钥存在状态，绝对禁止向下游接口明文回传 `encrypted_api_key` 本身。
    """
    return {
        "id": config.id,
        "display_name": config.display_name,
        "provider": config.provider,
        "base_url": config.base_url,
        "chat_model": config.chat_model,
        "supports_image": config.supports_image,
        "supports_document": config.supports_document,
        "image_detection": _image_detection_payload(config),
        "supports_reasoning": config.supports_reasoning,
        "reasoning_type": config.reasoning_type,
        "reasoning_label": config.reasoning_label,
        "max_context": config.max_context,
        "default_temperature": config.default_temperature,
        "enabled": config.enabled,
        "is_default": config.is_default,
        "has_api_key": bool(config.encrypted_api_key),
    }


def user_model_snapshot(config: UserModelConfig | None) -> dict | None:
    """产生用于 Agent 发布版本快照中的非密钥模型信息字典，用于保障发布版本的前向不退化性。"""
    if not config:
        return None
    return {
        "id": config.id,
        "display_name": config.display_name,
        "provider": config.provider,
        "base_url": config.base_url,
        "chat_model": config.chat_model,
        "supports_image": config.supports_image,
        "supports_document": config.supports_document,
        "image_detection": _image_detection_payload(config),
        "supports_reasoning": config.supports_reasoning,
        "reasoning_type": config.reasoning_type,
        "reasoning_label": config.reasoning_label,
        "max_context": config.max_context,
        "default_temperature": config.default_temperature,
        "enabled": config.enabled,
        "is_default": config.is_default,
    }


def get_owned_user_model(db: Session, *, user_id: int, config_id: int) -> UserModelConfig | None:
    """租户隔离级获取用户私有模型。"""
    return db.query(UserModelConfig).filter(UserModelConfig.user_id == user_id, UserModelConfig.id == config_id).first()


def list_user_model_configs(db: Session, *, user_id: int) -> list[UserModelConfig]:
    """列出当前用户拥有的所有私有模型配置。"""
    return db.query(UserModelConfig).filter(UserModelConfig.user_id == user_id).order_by(UserModelConfig.id.asc()).all()


def create_user_model_config(db: Session, *, user_id: int, payload: dict) -> UserModelConfig:
    """
    新建用户私有模型配置。

    🛡️ 智能多模态自动探测：
        在写入前自动发起针对该端点的多模态能力探测，自动判断其是否真能接收图片输入，强制矫正 `supports_image`。
    """
    api_key = _required_api_key(payload.get("api_key"))
    data = _config_fields(payload)
    data["supports_image"] = detect_image_support_for_payload(api_key=api_key, data=data)
    if data.get("is_default"):
        _clear_other_defaults(db, user_id=user_id, keep_id=None)
    config = UserModelConfig(
        user_id=user_id,
        encrypted_api_key=encrypt_api_key(api_key),
        **data,
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


def update_user_model_config(db: Session, *, config: UserModelConfig, payload: dict) -> UserModelConfig:
    """
    修改用户私有模型配置。
    
    🛡️ 安全控制与自适应能力检验：
        当涉及网关或模型变更时，重新触发针对当前端点多模态的自动嗅探，保持能力标签实时性。
    """
    should_probe_image = any(key in payload for key in ("api_key", "base_url", "chat_model", "supports_image"))
    if "api_key" in payload:
        api_key = payload["api_key"]
        if api_key is None or not str(api_key).strip():
            raise ValueError("API key cannot be empty")
        config.encrypted_api_key = encrypt_api_key(str(api_key))
    fields = _config_fields(payload, partial=True)
    if fields.get("is_default"):
        _clear_other_defaults(db, user_id=config.user_id, keep_id=config.id)
    if should_probe_image:
        probe_data = _config_data_for_probe(config, fields)
        fields["supports_image"] = detect_image_support_for_payload(
            api_key=decrypt_api_key(config.encrypted_api_key),
            data=probe_data,
        )
    for key, value in fields.items():
        setattr(config, key, value)
    db.commit()
    db.refresh(config)
    return config


def delete_user_model_config(db: Session, *, config: UserModelConfig) -> None:
    """
    删除私有模型配置。
    🛡️ 防崩溃防线：判定是否有草稿态 Agent 正处于使用当前配置的状态。如有，拒绝物理删除，维护全局外键参照完备性。
    """
    if (
        db.query(Agent.id)
        .filter(
            Agent.created_by == config.user_id,
            Agent.user_model_config_id == config.id,
        )
        .first()
    ):
        raise ValueError("Model config is in use")
    db.delete(config)
    db.commit()


def _image_detection_payload(config: UserModelConfig) -> dict:
    """封装多模态检测状态回执。"""
    return {
        "tested": True,
        "confirmed": bool(config.supports_image),
        "status": "confirmed" if config.supports_image else "failed",
        "source": "backend_probe",
    }


def resolve_user_model_config(
    db: Session,
    *,
    user_id: int,
    config_id: int | None,
    enabled_only: bool = True,
) -> UserModelConfig | None:
    """
    自适应路由用户的私有模型。
    若 `config_id` 未指定，则漂移降级获取用户的 `is_default` 默认模型。
    """
    query = db.query(UserModelConfig).filter(UserModelConfig.user_id == user_id)
    if config_id:
        query = query.filter(UserModelConfig.id == config_id)
    else:
        query = query.filter(UserModelConfig.is_default.is_(True))
    if enabled_only:
        query = query.filter(UserModelConfig.enabled.is_(True))
    return query.order_by(UserModelConfig.id.asc()).first()


def user_model_runtime_config(config: UserModelConfig) -> dict:
    """
    生成注入大模型调用提供商（llm.py）运行时解密后的上下文配置。
    包含完整解密出明文的 `api_key`，生命周期仅局限在本次调用链中，用完即弃，防泄露。
    """
    return {
        "provider": config.provider,
        "base_url": config.base_url,
        "api_key": decrypt_api_key(config.encrypted_api_key),
        "chat_model": config.chat_model,
        "supports_image": config.supports_image,
        "supports_document": config.supports_document,
        "supports_reasoning": config.supports_reasoning,
        "reasoning_type": config.reasoning_type,
        "reasoning_label": config.reasoning_label,
        "max_context": config.max_context,
        "default_temperature": config.default_temperature,
    }


def test_user_model_config(config: UserModelConfig, *, detect_image: bool = False) -> dict:
    """
    对用户私有模型配置进行全链路真实连通性测试。

    🎯 意图与工程大局观：
        在保存或测试私有模型时，发起真实的空问答 HTTP 请求测试，评估 Base URL 和 API Key 的正确性。
        如果参数 `detect_image` 为 True，将触发核心的 **Active Multi-Modal Probe（多模态主动探测）**，确保平台拿到的能力清单百分之百真实。
    """
    started = time.monotonic()
    checks = {
        "chat": {"ok": False, "required": True},
        "image": {
            "ok": False,
            "required": False,
            "declared": bool(config.supports_image),
            "tested": False,
            "status": "declared" if config.supports_image else "not_tested",
        },
        "reasoning": {
            "ok": bool(config.supports_reasoning and config.reasoning_type != "none"),
            "required": False,
            "type": config.reasoning_type,
        },
    }
    if detect_image:
        checks["image"]["detected"] = True
    try:
        runtime = user_model_runtime_config(config)
        provider = OpenAICompatibleProvider()
        # 1. 真实问答联通探测
        provider.chat(
            [{"role": "user", "content": "connection test"}],
            model=runtime["chat_model"],
            temperature=0,
            runtime_config=runtime,
        )
        checks["chat"]["ok"] = True
        
        # 2. 多模态图像能力测试
        if detect_image:
            image_result = _check_image_capability(provider, runtime, required=False)
            image_ok = image_result["ok"]
            checks["image"]["tested"] = True
            checks["image"]["ok"] = image_ok
            checks["image"]["status"] = "confirmed" if image_ok else "failed"
            if not image_ok:
                checks["image"]["error_code"] = image_result["error_code"]
                checks["image"]["message"] = image_result["message"]
    except Exception as exc:
        # 🛡️ 安全核心：在连通性抛错时进行严格过滤清洗
        checks["chat"]["error_code"] = _chat_probe_error_code(exc)
        checks["chat"]["message"] = _sanitize_probe_error(exc)
        return {
            "ok": False,
            "model": config.chat_model,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "message": _checks_message(checks),
            "error_code": "provider_error",
            "checks": checks,
            "detected_capabilities": _detected_capabilities(checks),
        }
    ok = all(not result.get("required") or result.get("ok") for result in checks.values())
    return {
        "ok": ok,
        "model": config.chat_model,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "message": _checks_message(checks),
        "checks": checks,
        "detected_capabilities": _detected_capabilities(checks),
    }


def test_user_model_payload(payload: dict, *, detect_image: bool = False) -> dict:
    """基于前端传入的临时 Payload（未入库数据）进行连通性预测试。"""
    api_key = _required_api_key(payload.get("api_key"))
    data = _config_fields(payload)
    config = UserModelConfig(
        user_id=0,
        encrypted_api_key=encrypt_api_key(api_key),
        **data,
    )
    return test_user_model_config(config, detect_image=detect_image)


def probe_models_payload(payload: dict) -> dict:
    """
    拉取 base_url 端点支持的模型列表(GET /models)。

    🎯 让用户只填 base_url + api_key 即可下拉选 model,无需手记模型串(最易错项)。
    兼容 OpenAI /v1/models 标准:返回 {data:[{id:...}]}。
    """
    base_url = (payload.get("base_url") or "").strip().rstrip("/")
    api_key = _required_api_key(payload.get("api_key"))
    if not base_url:
        raise ValueError("base_url is required")
    try:
        resp = httpx.get(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        models = sorted(m.get("id") for m in (data.get("data") or []) if m.get("id"))
        return {"ok": True, "models": models, "count": len(models)}
    except Exception as exc:
        return {"ok": False, "models": [], "count": 0, "message": _sanitize_probe_error(exc)}


def detect_image_support_for_payload(*, api_key: str, data: dict) -> bool:
    """基于载荷内容检测图像能力。"""
    config = UserModelConfig(
        user_id=0,
        encrypted_api_key=encrypt_api_key(api_key),
        **{**data, "supports_image": False},
    )
    runtime = user_model_runtime_config(config)
    return _check_image_capability(OpenAICompatibleProvider(), runtime, required=False)["ok"]


def _required_api_key(value) -> str:
    if value is None or not str(value).strip():
        raise ValueError("API key cannot be empty")
    return str(value).strip()


def _config_fields(payload: dict, *, partial: bool = False) -> dict:
    """清洗并规范入参。"""
    allowed = {
        "display_name",
        "provider",
        "base_url",
        "chat_model",
        "supports_image",
        "supports_document",
        "supports_reasoning",
        "reasoning_type",
        "reasoning_label",
        "max_context",
        "default_temperature",
        "enabled",
        "is_default",
    }
    defaults = {
        "provider": "openai-compatible",
        "supports_image": False,
        "supports_document": True,
        "supports_reasoning": False,
        "reasoning_type": "none",
        "reasoning_label": "不支持",
        "max_context": 131072,
        "default_temperature": 0.4,
        "enabled": True,
        "is_default": False,
    }
    data = {key: payload[key] for key in allowed if key in payload and payload[key] is not None}
    if not partial:
        data = {**defaults, **data}
    for key in ["display_name", "provider", "base_url", "chat_model"]:
        if key in data:
            data[key] = str(data[key]).strip()
    if "reasoning_type" in data:
        data["reasoning_type"] = _reasoning_type(data["reasoning_type"])
        data["supports_reasoning"] = data["reasoning_type"] != "none"
    elif "supports_reasoning" in data:
        data["supports_reasoning"] = bool(data["supports_reasoning"])
        data["reasoning_type"] = "prompt" if data["supports_reasoning"] else "none"
    if "reasoning_label" in data:
        data["reasoning_label"] = str(data["reasoning_label"] or "").strip() or _reasoning_label(data.get("reasoning_type", "none"))
    elif "reasoning_type" in data:
        data["reasoning_label"] = _reasoning_label(data["reasoning_type"])
    required = ["display_name", "base_url", "chat_model"]
    if not partial and any(not data.get(key) for key in required):
        raise ValueError("Invalid model config")
    if any(key in data and not data[key] for key in required):
        raise ValueError("Invalid model config")
    if "provider" in data and data["provider"] != "openai-compatible":
        raise ValueError("Invalid model config")
    return data


def _config_data_for_probe(config: UserModelConfig, fields: dict) -> dict:
    return {
        "display_name": fields.get("display_name", config.display_name),
        "provider": fields.get("provider", config.provider),
        "base_url": fields.get("base_url", config.base_url),
        "chat_model": fields.get("chat_model", config.chat_model),
        "supports_document": fields.get("supports_document", config.supports_document),
        "supports_reasoning": fields.get("supports_reasoning", config.supports_reasoning),
        "reasoning_type": fields.get("reasoning_type", config.reasoning_type),
        "reasoning_label": fields.get("reasoning_label", config.reasoning_label),
        "max_context": fields.get("max_context", config.max_context),
        "default_temperature": fields.get("default_temperature", config.default_temperature),
        "enabled": fields.get("enabled", config.enabled),
        "is_default": fields.get("is_default", config.is_default),
    }


def _reasoning_type(value) -> str:
    normalized = str(value or "none").strip()
    if normalized not in {"native", "prompt", "none"}:
        raise ValueError("Invalid model config")
    return normalized


def _reasoning_label(reasoning_type: str) -> str:
    return {"native": "深度思考", "prompt": "提示词增强", "none": "不支持"}.get(reasoning_type, "不支持")


def _tiny_png_data_url() -> str:
    """
    🧠 极简无损 1x1 像素 PNG Base64 数据 URL。
    作为主动多模态图像探测的最小有效载荷负载，最大程度压缩请求开销，保护网络带宽。
    """
    return (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )


def _check_image_capability(provider: OpenAICompatibleProvider, runtime: dict, *, required: bool) -> dict:
    """
    通过实际向该端点灌入 1x1 小图片，看其是否抛出 "Payload Rejected" 异常来最终核对多模态能力。
    """
    if get_settings().mock_llm:
        ok = _model_name_implies_image(runtime.get("chat_model", ""))
        return {
            "ok": ok,
            "error_code": "" if ok else "mock_model_name_not_vision",
            "message": "" if ok else "Mock image probe treats this model name as text-only",
        }
    try:
        # 发起多模态测试调用
        provider.chat(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe this image briefly"},
                        {"type": "image_url", "image_url": {"url": _tiny_png_data_url()}},
                    ],
                }
            ],
            model=runtime["chat_model"],
            temperature=0,
            runtime_config=runtime,
        )
        return {"ok": True, "error_code": "", "message": ""}
    except Exception as exc:
        if required:
            raise
        return {
            "ok": False,
            "error_code": _image_probe_error_code(exc),
            "message": _sanitize_probe_error(exc),
        }


def _image_probe_error_code(exc: Exception) -> str:
    text = str(exc).lower()
    status = _http_status_from_error(text)
    if status in {401, 403}:
        return "auth_failed"
    if status == 404:
        return "model_not_found"
    if status in {408, 429, 500, 502, 503, 504}:
        return "gateway_unavailable"
    if status == 400 or "invalid" in text or "image_url" in text or "content" in text:
        return "image_payload_rejected"
    if "invalid api key" in text or "unauthorized" in text or "forbidden" in text:
        return "auth_failed"
    if "model" in text and "not" in text:
        return "model_not_found"
    if "cannot connect" in text or "timed out" in text or "timeout" in text:
        return "gateway_unreachable"
    return "image_probe_failed"


def _chat_probe_error_code(exc: Exception) -> str:
    text = str(exc).lower()
    status = _http_status_from_error(text)
    if status in {401, 403}:
        return "auth_failed"
    if status == 404:
        return "model_not_found"
    if status in {408, 429, 500, 502, 503, 504}:
        return "gateway_unavailable"
    if status == 400 or "invalid" in text:
        return "chat_payload_rejected"
    if "invalid api key" in text or "unauthorized" in text or "forbidden" in text:
        return "auth_failed"
    if "model" in text and "not" in text:
        return "model_not_found"
    if "cannot connect" in text or "timed out" in text or "timeout" in text:
        return "gateway_unreachable"
    return "chat_probe_failed"


def _http_status_from_error(text: str) -> int | None:
    match = re.search(r"http\s+(\d{3})", text)
    return int(match.group(1)) if match else None


def _sanitize_probe_error(exc: Exception) -> str:
    """
    🛡️ 极度关键的安全性隐私红线防护。
    
    🎯 意图与工程大局观：
        用户提供的第三方模型端点如果返回连接报错（HTTPError），报错中经常会直接泄漏 HTTP 请求的 Header 信息。
        这就意味着用户的**明文 API_KEY (sk-...) 或 Bearer Token 可能会被直接注入报错 Traceback 并返回给前端浏览器展示**，甚至直接刷入服务器的 Error 日志中，存在极其巨大的安全外泄漏洞风险。
        
    🛡️ 过滤机制：
        利用严密正则表达式，强制拦截所有 `sk-...` 前缀的 OpenAI 密钥、Bearer 字段及 Base64 密钥段，
        将其统一物理净化脱敏替换为安全的 `[secret]`，字数最大严格限制为 500 字，阻断一切凭证意外泄露的可能。
    """
    message = str(exc)
    message = re.sub(r"(?i)(sk-[A-Za-z0-9_-]+|api[_-]?key\s*[:=]\s*\S+|authorization\s*:\s*\S+|bearer\s+\S+)", "[secret]", message)
    message = re.sub(r"\s+", " ", message).strip()
    return message[:500] or "Image probe failed"


def _model_name_implies_image(model_name: str) -> bool:
    """通过模型名称词义推断其是否内置支持多模态（vl、vision、omni 等标记）。"""
    normalized = f"-{str(model_name or '').lower().replace('_', '-')}-"
    markers = (
        "-vl-",
        "-vision-",
        "-visual-",
        "-multimodal-",
        "-omni-",
        "-qvq-",
        "-4v-",
    )
    return any(marker in normalized for marker in markers)


def _detected_capabilities(checks: dict) -> dict:
    """整合检测的推理和图像支持状态。"""
    reasoning_type = checks.get("reasoning", {}).get("type") or "none"
    supports_reasoning = bool(checks.get("reasoning", {}).get("ok")) and reasoning_type != "none"
    image_check = checks.get("image", {})
    image_confirmed = bool(image_check.get("ok"))
    return {
        "supports_text": bool(checks.get("chat", {}).get("ok")),
        "chat_error_code": checks.get("chat", {}).get("error_code", ""),
        "chat_error": checks.get("chat", {}).get("message", ""),
        "supports_image": image_confirmed,
        "image_confirmed": image_confirmed,
        "image_declared": bool(image_check.get("declared")),
        "image_status": image_check.get("status", "not_tested"),
        "image_error_code": image_check.get("error_code", ""),
        "image_error": image_check.get("message", ""),
        "supports_reasoning": supports_reasoning,
        "reasoning_type": reasoning_type if supports_reasoning else "none",
    }


def _checks_message(checks: dict) -> str:
    """聚合检测结论信息。"""
    failed = [name for name, result in checks.items() if result.get("required") and not result.get("ok")]
    if failed:
        return "Capability check failed: " + ", ".join(failed)
    passed = [name for name, result in checks.items() if result.get("required") and result.get("ok")]
    return "Capability check succeeded: " + ", ".join(passed)


def _clear_other_defaults(db: Session, *, user_id: int, keep_id: int | None) -> None:
    """
    物理重置该用户名下的其他所有配置为非默认模型状态。
    使用 synchronize_session=False 实现高效的数据库批量更新事务。
    """
    query = db.query(UserModelConfig).filter(UserModelConfig.user_id == user_id, UserModelConfig.is_default.is_(True))
    if keep_id is not None:
        query = query.filter(UserModelConfig.id != keep_id)
    query.update({UserModelConfig.is_default: False}, synchronize_session=False)
