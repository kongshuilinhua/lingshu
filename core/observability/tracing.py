"""Langfuse 链路追踪（软依赖）。

🎯 为什么收拢在这一个模块：
    模型调用层是自写的（core/integrations/llm.py，非 OpenAI SDK / LangChain），Langfuse 的自动包装器抓不到，
    只能在调用层出口手动埋点。业务代码只用这里的 trace / span / generation / score，从不直接碰 Langfuse API——
    SDK 升级时只改本文件（RAGFlow 曾因 SDK 升级导致手写 generation 调用整体报错，就是反例）。

🛡️ 软依赖与 fail-open：
    - 未安装 langfuse、或未配置 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL 时，全部退化为 no-op。
    - 埋点自身的任何异常都被吞掉并记日志，绝不影响对话主流程；上报由 SDK 后台线程批量异步发送。
    - 上报前统一脱敏：密钥类字段打码、图片 data URL 换占位、超长文本截断（见 _mask）。

层级（依赖 OTel 上下文自动嵌套）：
    trace（一次工作流运行，session=会话）→ span（节点 / 检索 / 工具）→ generation（模型调用）/ embedding
"""

from __future__ import annotations

import contextlib
import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_MAX_TEXT = 8000
_MAX_ITEMS = 60
_SECRET_KEYS = re.compile(r"(api[_-]?key|secret|token|password|authorization|cookie|credential)", re.I)
_DATA_URL = re.compile(r"data:([\w/+.-]+);base64,[A-Za-z0-9+/=]{64,}")
_BEARER = re.compile(r"(Bearer\s+|sk-[A-Za-z0-9]{2})[A-Za-z0-9._\-]{8,}")

_lock = threading.Lock()
_client = None
_initialized = False
# 仅测试使用：注入 OTel span exporter（如 InMemorySpanExporter），不连真实 Langfuse 服务
_test_span_exporter = None


def _settings():
    from core.config import get_settings

    return get_settings()


def _mask(*, data: Any, **_: Any) -> Any:
    """上报前脱敏：递归处理 dict/list，截断长文本，替换图片 base64，遮蔽疑似密钥。"""
    return _scrub(data, depth=0)


def _scrub(value: Any, *, depth: int) -> Any:
    if depth > 8:
        return "[depth-limit]"
    if isinstance(value, str):
        text = _DATA_URL.sub(lambda m: f"[{m.group(1)} base64 omitted]", value)
        text = _BEARER.sub(lambda m: m.group(1) + "***", text)
        return text if len(text) <= _MAX_TEXT else text[:_MAX_TEXT] + f"…[truncated {len(text) - _MAX_TEXT} chars]"
    if isinstance(value, dict):
        out = {}
        for key, item in list(value.items())[:_MAX_ITEMS]:
            out[key] = "***" if isinstance(key, str) and _SECRET_KEYS.search(key) and isinstance(item, str) else _scrub(item, depth=depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        items = [_scrub(item, depth=depth + 1) for item in list(value)[:_MAX_ITEMS]]
        if len(value) > _MAX_ITEMS:
            items.append(f"…[{len(value) - _MAX_ITEMS} more]")
        return items
    return value


def client():
    """惰性初始化 Langfuse 客户端；未启用时返回 None。"""
    global _client, _initialized
    if _initialized:
        return _client
    with _lock:
        if _initialized:
            return _client
        _initialized = True
        settings = _settings()
        public_key = getattr(settings, "langfuse_public_key", None)
        secret_key = getattr(settings, "langfuse_secret_key", None)
        base_url = getattr(settings, "langfuse_base_url", None)
        if not (public_key and secret_key and base_url):
            return None
        if getattr(settings, "mock_llm", False):
            # mock 模式（测试 / 无密钥联调）的调用没有观测价值，且不应把测试流量写进监控
            return None
        try:
            from langfuse import Langfuse
        except Exception:  # pragma: no cover - 取决于是否安装 langfuse
            logger.warning("LANGFUSE_* 已配置但未安装 langfuse，链路追踪不可用")
            return None
        try:
            _client = Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                base_url=base_url,
                environment=getattr(settings, "langfuse_environment", None) or None,
                sample_rate=float(getattr(settings, "langfuse_sample_rate", 1.0) or 1.0),
                mask=_mask,
                **({"span_exporter": _test_span_exporter} if _test_span_exporter is not None else {}),
                # 只导出本模块创建的 span，避免把其他 OTel 埋点（若有）一起送进 Langfuse
                should_export_span=lambda span: bool(span.instrumentation_scope and span.instrumentation_scope.name == "langfuse-sdk"),
            )
        except Exception:
            logger.exception("Langfuse 初始化失败，链路追踪不可用")
            _client = None
        return _client


def enabled() -> bool:
    return client() is not None


def reset_for_tests(span_exporter=None) -> None:
    global _client, _initialized, _test_span_exporter
    with _lock:
        if _client is not None:
            try:
                _client.shutdown()
            except Exception:
                pass
        _client, _initialized, _test_span_exporter = None, False, span_exporter


class _Noop:
    """未启用追踪时的占位对象：与 Observation 接口一致，任何调用都不做事。"""

    id = None
    trace_id = None

    def update(self, **_: Any) -> "_Noop":
        return self

    def end(self, **_: Any) -> "_Noop":
        return self

    def mark_first_token(self) -> None:
        return None

    def error(self, exc: BaseException) -> None:
        return None


NOOP = _Noop()


class Observation:
    """对 Langfuse 观测对象的薄封装：所有方法都吞异常，保证埋点不会打断业务。"""

    def __init__(self, inner):
        self._inner = inner
        self.id = getattr(inner, "id", None)
        self.trace_id = getattr(inner, "trace_id", None)
        self._first_token_marked = False

    def update(self, **kwargs: Any) -> "Observation":
        try:
            self._inner.update(**{key: value for key, value in kwargs.items() if value is not None})
        except Exception:
            logger.debug("Langfuse update 失败", exc_info=True)
        return self

    def mark_first_token(self) -> None:
        """流式首帧到达：记录 completion_start_time，Langfuse 据此算 TTFT。"""
        if self._first_token_marked:
            return
        self._first_token_marked = True
        self.update(completion_start_time=datetime.now(timezone.utc))

    def error(self, exc: BaseException) -> None:
        self.update(level="ERROR", status_message=f"{type(exc).__name__}: {exc}"[:500])


@contextlib.contextmanager
def _observe(name: str, as_type: str, **kwargs: Any) -> Iterator[Any]:
    langfuse = client()
    if langfuse is None:
        yield NOOP
        return
    try:
        # 只挂在已有 trace 下：入库批量向量化、健康探测、连接测试等游离调用不单独成 trace，避免刷屏
        if not langfuse.get_current_trace_id():
            yield NOOP
            return
    except Exception:
        yield NOOP
        return
    try:
        manager = langfuse.start_as_current_observation(name=name, as_type=as_type,
                                                        **{key: value for key, value in kwargs.items() if value is not None})
        inner = manager.__enter__()
    except Exception:
        logger.debug("Langfuse 创建观测失败", exc_info=True)
        yield NOOP
        return
    observation = Observation(inner)
    try:
        yield observation
    except BaseException as exc:
        # GeneratorExit：流式响应被客户端中断，不算错误
        if not isinstance(exc, GeneratorExit):
            observation.error(exc)
        raise
    finally:
        try:
            manager.__exit__(None, None, None)
        except Exception:
            logger.debug("Langfuse 结束观测失败", exc_info=True)


def span(name: str, *, as_type: str = "span", input: Any = None, metadata: Any = None):
    """普通 span：工作流节点（span）、检索（retriever）、工具（tool）等。"""
    return _observe(name, as_type, input=input, metadata=metadata)


def generation(name: str, *, model: str | None, input: Any = None, model_parameters: dict | None = None,
               metadata: Any = None, as_type: str = "generation"):
    """模型调用。结束前用 update(output=..., usage_details=...) 回填结果与用量。"""
    params = {key: value for key, value in (model_parameters or {}).items() if value is not None} or None
    return _observe(name, as_type, model=model, input=input, model_parameters=params, metadata=metadata)


@contextlib.contextmanager
def trace(name: str, *, trace_id: str | None = None, user_id: Any = None, session_id: Any = None,
          tags: list[str] | None = None, metadata: dict | None = None, input: Any = None) -> Iterator[Any]:
    """
    一次工作流运行的根 trace：session_id / user_id / tags 通过 propagate_attributes 传给所有子观测。

    trace_id 可由调用方用 trace_id_for() 派生（如按 assistant 消息无关的 run id），便于事后给 trace 打分。
    """
    langfuse = client()
    if langfuse is None:
        yield NOOP
        return
    try:
        from langfuse import propagate_attributes

        attributes = propagate_attributes(
            user_id=str(user_id) if user_id is not None else None,
            session_id=str(session_id) if session_id is not None else None,
            tags=tags,
            # trace 级 metadata 只接受短字符串值
            metadata={key: str(value)[:200] for key, value in (metadata or {}).items() if value is not None} or None,
            trace_name=name,
        )
        manager = langfuse.start_as_current_observation(
            name=name, as_type="agent", input=input,
            trace_context={"trace_id": trace_id} if trace_id else None,
        )
        inner = manager.__enter__()
        attributes.__enter__()
    except Exception:
        logger.debug("Langfuse 创建 trace 失败", exc_info=True)
        yield NOOP
        return
    observation = Observation(inner)
    try:
        yield observation
    except BaseException as exc:
        if not isinstance(exc, GeneratorExit):
            observation.error(exc)
        raise
    finally:
        for closer in (attributes, manager):
            try:
                closer.__exit__(None, None, None)
            except Exception:
                logger.debug("Langfuse 结束 trace 失败", exc_info=True)


def trace_id_for(seed: str) -> str | None:
    """由业务 ID 确定性派生 trace id（同一 seed 恒得同一 id）；未启用时返回 None。"""
    langfuse = client()
    if langfuse is None:
        return None
    try:
        return langfuse.create_trace_id(seed=seed)
    except Exception:
        return None


def score(*, trace_id: str | None, name: str, value: float | str, comment: str | None = None,
          data_type: str | None = None) -> None:
    """给 trace 打分（如用户点赞/点踩）。"""
    langfuse = client()
    if langfuse is None or not trace_id:
        return
    try:
        langfuse.create_score(trace_id=trace_id, name=name, value=value, comment=comment, data_type=data_type)
    except Exception:
        logger.debug("Langfuse 打分失败", exc_info=True)


def langchain_callbacks() -> list:
    """LangChain 辅助调用（记忆、查询理解、自纠检索）用的回调，挂到当前 trace 下；未启用时为空。"""
    if client() is None:
        return []
    try:
        from langfuse.langchain import CallbackHandler

        return [CallbackHandler()]
    except Exception:
        logger.debug("Langfuse LangChain 回调不可用", exc_info=True)
        return []


def bind_context(generator):
    """
    让同步生成器的每次 next() 都在同一个 contextvars.Context 里执行。

    🛡️ 为什么需要：StreamingResponse 迭代同步生成器时，Starlette 用 anyio 在线程池里逐次调用 next()，
        而 anyio 每次都 copy_context()——生成器在某次 next() 里打开的 span（OTel 上下文）到下一次 next()
        就丢了，子观测挂不到父节点上，结束时还会报「token 来自另一个 Context」。
        这里固定一个 Context，所有 next() / close() 都在其中运行（同一时刻只有一个线程进入，满足 Context 约束）。
    """
    import contextvars

    context = contextvars.copy_context()
    try:
        while True:
            try:
                item = context.run(next, generator)
            except StopIteration:
                return
            yield item
    finally:
        context.run(generator.close)


def flush() -> None:
    langfuse = client()
    if langfuse is None:
        return
    try:
        langfuse.flush()
    except Exception:
        logger.debug("Langfuse flush 失败", exc_info=True)


def shutdown() -> None:
    langfuse = client()
    if langfuse is None:
        return
    try:
        langfuse.shutdown()
    except Exception:
        logger.debug("Langfuse shutdown 失败", exc_info=True)
