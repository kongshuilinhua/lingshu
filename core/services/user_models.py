from __future__ import annotations

import ipaddress
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from sqlalchemy.orm import Session

from core.config import get_settings
from core.db.models import Agent, UserModelConfig
from core.integrations.llm import OpenAICompatibleProvider
from core.integrations import anthropic_protocol
from core.security.api_keys import decrypt_api_key, encrypt_api_key
from core.security.outbound_http import open_model_gateway


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

    能力（图片、深度思考）以用户声明为准：前端按模型名预填、用户可改，后端不再自动探测。
    """
    api_key = _required_api_key(payload.get("api_key"))
    data = _config_fields(payload)
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
    修改用户私有模型配置；能力字段按用户提交的值保存。
    """
    if "api_key" in payload:
        api_key = payload["api_key"]
        if api_key is None or not str(api_key).strip():
            raise ValueError("API key cannot be empty")
        config.encrypted_api_key = encrypt_api_key(str(api_key))
    fields = _config_fields(payload, partial=True)
    if fields.get("is_default"):
        _clear_other_defaults(db, user_id=config.user_id, keep_id=config.id)
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
        "untrusted_base_url": True,
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


_CONNECTION_TEST_MAX_TOKENS = 32


def test_user_model_config(config: UserModelConfig) -> dict:
    """
    连通性测试：发一个极短的流式问答，收到第一帧即判定 base_url、API Key 与模型名可用。

    只回答「能不能连上」，不探测图片/推理等能力——自动探测既慢又容易误判
    （如部分纯文本模型收到图片仍返回 200），能力改由用户声明。

    ⚡ 必须用流式并提前结束：推理模型（如硅基流动的 DeepSeek-R1）走非流式时，
    即便 max_tokens 很小也要等完整思考结束才返回，实测近 1 分钟；流式首帧不到 1 秒。
    """
    started = time.monotonic()
    result = {"ok": False, "model": config.chat_model}
    try:
        runtime = user_model_runtime_config(config)
        stream = OpenAICompatibleProvider().chat_stream(
            [{"role": "user", "content": "ping"}],
            model=runtime["chat_model"],
            temperature=0,
            runtime_config={**runtime, "max_tokens": _CONNECTION_TEST_MAX_TOKENS},
        )
        try:
            # 首帧到达即说明鉴权与模型名都已通过；关闭生成器会一并断开上游连接
            next(iter(stream), None)
        finally:
            stream.close()
        result.update(ok=True, message="连接成功")
    except Exception as exc:
        # 🛡️ 报错可能回显请求头里的密钥，必须脱敏后再返回前端
        result.update(error_code=_chat_probe_error_code(exc), message=_sanitize_probe_error(exc))
    result["latency_ms"] = int((time.monotonic() - started) * 1000)
    return result


def test_user_model_payload(payload: dict) -> dict:
    """基于前端传入的临时 Payload（未入库数据）进行连通性预测试。"""
    api_key = _required_api_key(payload.get("api_key"))
    data = _config_fields(payload)
    config = UserModelConfig(
        user_id=0,
        encrypted_api_key=encrypt_api_key(api_key),
        **data,
    )
    return test_user_model_config(config)


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
    _validate_public_base_url(base_url)
    provider = _protocol(payload.get("provider"))
    try:
        url = anthropic_protocol.endpoint(base_url, "models") if provider == "anthropic" else f"{base_url}/models"
        auth_headers = anthropic_protocol.headers(api_key) if provider == "anthropic" else {"Authorization": f"Bearer {api_key}"}
        models = set()
        cursors = set()
        for _ in range(20):
            request = urllib.request.Request(url, headers=auth_headers)
            with open_model_gateway(request, timeout=10, allow_private=_private_gateways_allowed()) as response:
                if response.status != 200:
                    raise ValueError("Model endpoint redirected or rejected the request")
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError("Model list response is too large")
            data = json.loads(raw)
            models.update(item["id"] for item in (data.get("data") or [])
                          if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"])
            if provider != "anthropic" or not data.get("has_more") or len(models) >= 500:
                ordered = sorted(models)[:500]
                return {"ok": True, "models": ordered, "count": len(ordered)}
            cursor = data.get("last_id")
            if not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise ValueError("Invalid model list pagination")
            cursors.add(cursor)
            url = anthropic_protocol.endpoint(base_url, "models") + "?" + urllib.parse.urlencode({"after_id": cursor})
        raise ValueError("Model list pagination limit exceeded")
    except urllib.error.HTTPError as exc:
        return {"ok": False, "models": [], "count": 0, "message": _sanitize_probe_error(_http_error_with_detail(exc))}
    except Exception as exc:
        return {"ok": False, "models": [], "count": 0, "message": _sanitize_probe_error(exc)}


def _http_error_with_detail(exc: urllib.error.HTTPError) -> Exception:
    """把上游错误体里的 message 拼进异常文本：只有状态行（如 401 Authorization Required）时用户无从判断原因。"""
    try:
        body = exc.read(4096).decode("utf-8", errors="replace")
    except Exception:
        return exc
    detail = body.strip()
    try:
        data = json.loads(body)
        error = data.get("error") if isinstance(data, dict) else None
        detail = (error.get("message") if isinstance(error, dict) else error) or data.get("message") or detail
    except (ValueError, AttributeError):
        pass
    detail = re.sub(r"\s+", " ", str(detail or "")).strip()[:300]
    return RuntimeError(f"HTTP {exc.code}: {detail}" if detail else f"HTTP {exc.code}: {exc.reason}")


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
    if "base_url" in data:
        _validate_public_base_url(data["base_url"])
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
    if "provider" in data:
        data["provider"] = _protocol(data["provider"])
    return data


_PRIVATE_GATEWAY_HINT = " (private or self-hosted gateways require USER_MODEL_ALLOW_PRIVATE_NETWORK=true)"


def _private_gateways_allowed() -> bool:
    return bool(getattr(get_settings(), "user_model_allow_private_network", False))


def _validate_public_base_url(value: str) -> None:
    parsed = urllib.parse.urlsplit(value)
    allow_private = _private_gateways_allowed()
    allowed_schemes = {"http", "https"} if allow_private else {"https"}
    if (parsed.scheme not in allowed_schemes or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("Public HTTPS base_url required" + ("" if allow_private else _PRIVATE_GATEWAY_HINT))
    if parsed.hostname.rstrip(".").lower() in {"metadata", "metadata.google.internal"}:
        raise ValueError("Model gateway target is blocked")
    try:
        ip = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return
    mapped = getattr(ip, "ipv4_mapped", None) or ip
    if mapped.is_link_local or mapped.is_multicast or mapped.is_unspecified:
        raise ValueError("Model gateway target is blocked")
    if not allow_private and not ip.is_global:
        raise ValueError("Public HTTPS base_url required" + _PRIVATE_GATEWAY_HINT)


def _protocol(value) -> str:
    value = str(value or "openai-compatible").strip()
    if value == "openai":
        value = "openai-compatible"
    if value not in {"openai-compatible", "anthropic"}:
        raise ValueError("Unsupported model protocol")
    return value


def _reasoning_type(value) -> str:
    normalized = str(value or "none").strip()
    if normalized not in {"native", "prompt", "none"}:
        raise ValueError("Invalid model config")
    return normalized


def _reasoning_label(reasoning_type: str) -> str:
    return {"native": "深度思考", "prompt": "提示词增强", "none": "不支持"}.get(reasoning_type, "不支持")


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
    return message[:500] or "Connection test failed"


def _clear_other_defaults(db: Session, *, user_id: int, keep_id: int | None) -> None:
    """
    物理重置该用户名下的其他所有配置为非默认模型状态。
    使用 synchronize_session=False 实现高效的数据库批量更新事务。
    """
    query = db.query(UserModelConfig).filter(UserModelConfig.user_id == user_id, UserModelConfig.is_default.is_(True))
    if keep_id is not None:
        query = query.filter(UserModelConfig.id != keep_id)
    query.update({UserModelConfig.is_default: False}, synchronize_session=False)
