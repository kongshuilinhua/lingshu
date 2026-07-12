from __future__ import annotations

import hashlib
import json
import re
import socket
import ssl
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass

from core.config import get_settings
from core.integrations.circuit_breaker import CircuitBreaker, RedisCircuitBreaker

# 🎯 系统硬编码默认的 OpenAI 兼容模式 API 端点（指向阿里云百炼/通义千问兼容接口）
DASHSCOPE_COMPATIBLE_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
OPENAI_COMPATIBLE_DEFAULT_BASE = DASHSCOPE_COMPATIBLE_BASE


@dataclass
class ChatResponse:
    """
    统一的智能体文本响应实体类。
    
    🎯 意图与工程大局观：
        抹平各大模型厂商返回格式的微观差异，为上层 Agent 提供标准格式。
        支持携带内容 `content` 和工具调用列表 `tool_calls`。
    """
    content: str | None = None
    tool_calls: list[dict] | None = None


# 🛡️ 兼容垫片：部分模型/网关（典型如 Qwen 系）不会把函数调用放进 OpenAI 标准的
# 结构化 `tool_calls` 字段，而是以训练时的文本格式直接写进 `content`，导致下游
# 工作流误把"调用指令"当成最终答案。解析器刻意做得宽容——这些第三方网关吐出的
# 文本格式既不稳定也常畸形（闭合标签数量对不上、用 <function=arguments> 包参数等），
# 所以不依赖标签配平，而是「定位函数名 → 在其区间内取参数」，把它们解析回标准
# tool_calls，恢复"按需调用"语义。
_TOOLCALL_TAG_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
_FUNC_OPEN_RE = re.compile(r"<function\s*=\s*([^>\s]+)\s*>", re.DOTALL)
_PARAMETER_TAG_RE = re.compile(r"<parameter\s*=\s*([^>\s]+)\s*>(.*?)</parameter>", re.DOTALL)
# 这些名字是"参数包装标签"而非真正的函数名，需从函数名候选里排除
_ARG_WRAPPER_NAMES = {"arguments", "parameters", "args", "params"}


def _coerce_param_value(raw: str):
    """参数值优先按 JSON 解析（数组/对象/数字/布尔），失败则保留原始字符串。"""
    raw = raw.strip()
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


def _first_json_object(text: str) -> dict | None:
    """从 text 中提取第一个大括号配平的 JSON 对象（容忍字符串内的花括号/转义）。"""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                        return obj if isinstance(obj, dict) else None
                    except (json.JSONDecodeError, ValueError):
                        break  # 该 { 不是合法对象，换下一个起点重试
        start = text.find("{", start + 1)
    return None


def _extract_text_tool_calls(content: str) -> list[dict]:
    """把 content 里的文本格式函数调用解析为标准 tool_calls 列表（无则返回空）。

    支持的格式：
    - <tool_call>{"name": "...", "arguments": {...}}</tool_call>
    - <function=NAME><parameter=PNAME>VALUE</parameter>...</function>          （Hermes 标准）
    - <function=NAME><function=arguments>{json}</function>                      （畸形变体，闭合标签可能缺失）
    - <function=NAME> ... {json} ...                                           （裸 JSON 参数兜底）
    """
    if not content:
        return []
    parsed: list[tuple[str, str]] = []  # (name, arguments_json_str)

    # 格式一：<tool_call>{json}</tool_call>
    for match in _TOOLCALL_TAG_RE.finditer(content):
        try:
            data = json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        name = str(data.get("name") or "").strip()
        if not name:
            continue
        arguments = data.get("arguments")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments if arguments is not None else {}, ensure_ascii=False)
        parsed.append((name, arguments))

    # 格式二/三/四：定位真正的函数名标签（排除 arguments 等参数包装标签），
    # 每个函数体范围为「本标签结束 ~ 下一个函数名标签开始」，在其中提取参数。
    name_tags = [
        (m.group(1).strip(), m.end()) for m in _FUNC_OPEN_RE.finditer(content)
        if m.group(1).strip().lower() not in _ARG_WRAPPER_NAMES
    ]
    starts = [m.start() for m in _FUNC_OPEN_RE.finditer(content) if m.group(1).strip().lower() not in _ARG_WRAPPER_NAMES]
    for index, (name, body_start) in enumerate(name_tags):
        if not name:
            continue
        body_end = starts[index + 1] if index + 1 < len(starts) else len(content)
        body = content[body_start:body_end]
        params = {pm.group(1).strip(): _coerce_param_value(pm.group(2)) for pm in _PARAMETER_TAG_RE.finditer(body)}
        if params:
            arguments = json.dumps(params, ensure_ascii=False)
        else:
            obj = _first_json_object(body)
            arguments = json.dumps(obj if isinstance(obj, dict) else {}, ensure_ascii=False)
        parsed.append((name, arguments))

    tool_calls = []
    for index, (name, arguments) in enumerate(parsed):
        digest = hashlib.md5(f"{index}:{name}:{arguments}".encode("utf-8")).hexdigest()[:12]
        tool_calls.append({
            "id": f"call_{digest}",
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        })
    return tool_calls


class OpenAICompatibleProvider:
    """
    标准 OpenAI 兼容模型提供商。

    🎯 意图与工程大局观：
        系统底层唯一且核心的通用 LLM 交互客户端。
        不仅支持同步问答（chat）、流式输出（chat_stream）、文本嵌入（embed），还前瞻性地内置了标准 RAG 重排器（rerank）接口。
        
    🛡️ 防御性设计：
        - 完全基于 Python 原生库 `urllib.request` 实现，不引入 `requests` 或 `httpx` 等外部网络库，保证系统核心引擎的极致轻量化与高可移植性。
        - 深度融合了测试开发模式（mock_llm），在没有 API 密钥或处于脱机开发测试场景下自动进行仿真响应，极大提升了测试反馈速度。
    """

    def __init__(self) -> None:
        # 🛡️ 调试标记：记录上一次交互是否由 Mock 仿真模块接管，便于单元测试进行状态断言
        self.last_chat_mock = False
        self.last_embed_mock = False
        # 🛡️ 三态熔断器：每个模型独立追踪健康状态
        self._breakers: dict[str, CircuitBreaker] = {}

    def chat(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        temperature: float = 0.4,
        runtime_config: dict | None = None,
        tools: list[dict] | None = None,
        thinking: bool = False,
    ) -> ChatResponse:
        """
        同步文本生成方法（支持 Tool Call 参数请求）。

        🧠 魔鬼数字与前沿技术参数：
            - `temperature` 默认 0.4: 处于确定性回答与创造性逻辑的均衡点，适合严谨的 Agent 编排流转。
            - 针对 Mock 模式，工具调用仿真生成确定性的哈希值作为 call_id，方便前后端状态回溯。

        🛡️ 防御性编程：
            - 对空 API_KEY 在执行前进行前置检查，避免发出无谓的 HTTP 请求。
            - `thinking` 默认 False：查询理解、工具决策、记忆摘要等内部调用不应触发混合推理模型的隐藏思考。
            - 即使传入 thinking=True，非流式请求也会被 `_apply_generation_limits` 强制关闭
              enable_thinking（DashScope 仅流式支持开启），深度思考轨迹只在流式链路生效。
        """
        settings = get_settings()
        api_key = self._api_key(settings, runtime_config, purpose="chat")
        if settings.mock_llm:
            self.last_chat_mock = True
            user_text = self._content_text(next((m["content"] for m in reversed(messages) if m.get("role") == "user"), ""))
            context_hint = " ".join(self._content_text(m.get("content", ""))[:500] for m in messages if m.get("role") == "system")
            if tools:
                tool_names = [t.get("function", {}).get("name", "") for t in tools]
                return ChatResponse(
                    tool_calls=[{
                        "id": f"mock_call_{hashlib.md5(user_text.encode()).hexdigest()[:8]}",
                        "type": "function",
                        "function": {"name": tool_names[0], "arguments": json.dumps({"query": user_text[:120]}, ensure_ascii=False)},
                    }]
                )
            return ChatResponse(content=f"Mock answer for: {user_text}\n\nContext summary: {context_hint[:2000]}")
        if not api_key:
            raise RuntimeError("Chat model API key is not configured")
        self.last_chat_mock = False

        # 🛡️ 熔断器检查：模型不可用时快速失败
        model_name = model or (runtime_config or {}).get("chat_model") or settings.openai_model
        breaker = self._breaker_for(model_name)
        if not breaker.allow_request():
            raise RuntimeError(f"Model '{model_name}' is temporarily unavailable (circuit breaker open)")

        url = self._api_base(settings, runtime_config, purpose="chat").rstrip("/") + "/chat/completions"
        payload: dict = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        self._apply_generation_limits(payload, settings, runtime_config, thinking)
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            data = self._post_json(url, payload, api_key)
        except Exception:
            breaker.record_failure()
            raise
        breaker.record_success()
        return self._parse_chat_response(data)

    def chat_stream(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        temperature: float = 0.4,
        runtime_config: dict | None = None,
        tools: list[dict] | None = None,
        thinking: bool = False,
    ) -> Iterable[dict]:
        """
        异步流式文本生成生成器（Server-Sent Events）。

        🎯 产出协议（帧协议）：
            - 每帧为 dict：`{"type": "content" | "reasoning", "text": str}`。
            - `content` 帧是回答正文 Token；`reasoning` 帧是原生推理模型（如 Qwen3 系列开启
              enable_thinking 后）的思考轨迹流，两条通道彻底分离，消费方绝不允许把
              reasoning 帧拼进最终回答（否则会污染存库正文与会话记忆）。

        🧠 深度思考（thinking）：
            - 对识别为 Qwen3 系列的模型显式下发 DashScope 的 `enable_thinking` 扩展参数：
              thinking=True 时开启思考流输出；thinking=False 时显式关闭，
              避免混合推理模型（如 Qwen3 系列）默认思考导致简单问题也长时间无首字输出。
            - 其他原生推理模型（如 deepseek-reasoner）默认自带思考流，无需额外参数，
              reasoning 帧解析对它们同样生效。

        ⚡ 边界与性能思考：
            - 利用 Python 的 `yield` 关键字返回生成器，支持逐字/逐词流式推送到前端，显著降低用户端首字延迟（TTFT）。
            - 流式输出在大批量并发处理下，能大幅平抑服务器网络 I/O 峰值吞吐，优化瞬时带宽占用。
        """
        settings = get_settings()
        api_key = self._api_key(settings, runtime_config, purpose="chat")
        if settings.mock_llm:
            self.last_chat_mock = True
            text = self.chat(messages, model=model, temperature=temperature, runtime_config=runtime_config, tools=tools, thinking=thinking)
            if text.tool_calls:
                yield {"type": "content", "text": json.dumps({"tool_calls": text.tool_calls}, ensure_ascii=False)}
                return
            if thinking:
                # 🛡️ 脱机联调对齐：thinking 开启时仿真一段思考流，保证前端/工作流的独立通道无 API Key 也可联调
                user_text = self._content_text(next((m["content"] for m in reversed(messages) if m.get("role") == "user"), ""))
                mock_reasoning = f"Mock reasoning for: {user_text}"
                for index in range(0, len(mock_reasoning), 24):
                    yield {"type": "reasoning", "text": mock_reasoning[index : index + 24]}
            for index in range(0, len(text.content or ""), 24):
                yield {"type": "content", "text": (text.content or "")[index : index + 24]}
            return
        if not api_key:
            raise RuntimeError("Chat model API key is not configured")
        self.last_chat_mock = False

        # 🛡️ 熔断器检查：模型不可用时快速失败
        model_name = model or (runtime_config or {}).get("chat_model") or settings.openai_model
        breaker = self._breaker_for(model_name)
        if not breaker.allow_request():
            raise RuntimeError(f"Model '{model_name}' is temporarily unavailable (circuit breaker open)")

        url = self._api_base(settings, runtime_config, purpose="chat").rstrip("/") + "/chat/completions"
        payload: dict = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        self._apply_generation_limits(payload, settings, runtime_config, thinking)
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        # 🎯 编排流转：有工具绑定时，实际上由工作流 runtime 模块使用非流式 chat() 做决策，流式仅在最后的最终回答生成阶段触发
        try:
            yield from self._post_json_stream(url, payload, api_key)
        except Exception:
            breaker.record_failure()
            raise
        breaker.record_success()

    def embed(self, text: str, *, runtime_config: dict | None = None) -> list[float]:
        """
        文本向量化嵌入（Embedding）。

        🧠 魔鬼数字与前沿技术参数：
            - Mock 向量生成机制：输出 32 维特征向量（数值区间为 [-1.0, 1.0]），确保能够被相似度计算方法（IP / Cosine）正常解析。
        """
        settings = get_settings()
        api_key = self._api_key(settings, runtime_config, purpose="embedding")
        if settings.mock_llm:
            self.last_embed_mock = True
            return self._mock_embed(text)
        if not api_key:
            raise RuntimeError("Embedding API key is not configured")
        self.last_embed_mock = False

        model = settings.openai_embedding_model
        # 🎯 缓存分层：query 向量化是热点读路径，命中则直接返回，省一次最贵的外部 API 调用。
        #    缓存不随知识库文档增删失效（同文本同模型向量恒定），命中率远高于整条检索结果缓存。
        cache_enabled = bool(getattr(settings, "embedding_cache_enabled", False))
        if cache_enabled:
            from core.observability.metrics import record_cache
            from core.services.rag_cache import redis_store

            cached = redis_store.get_embedding(model, text)
            record_cache("embedding", cached is not None)
            if cached is not None:
                return cached

        url = self._api_base(settings, runtime_config, purpose="embedding").rstrip("/") + "/embeddings"
        payload = {"model": model, "input": text}
        data = self._post_json(url, payload, api_key)
        vector = data["data"][0]["embedding"]
        if cache_enabled:
            redis_store.set_embedding(model, text, vector, settings.embedding_cache_ttl_seconds)
        return vector

    @staticmethod
    def _mock_embed(text: str) -> list[float]:
        """脱机/测试模式下的确定性 32 维 mock 向量（与 embed 单条逻辑一致，供批量复用）。"""
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [((digest[i % len(digest)] / 255.0) * 2) - 1 for i in range(32)]

    def embed_batch(self, texts: list[str], *, runtime_config: dict | None = None, batch_size: int | None = None) -> list[list[float]]:
        """批量文本向量化。OpenAI 兼容 embeddings 接口原生支持 list 输入，分批请求以降低往返次数。"""
        if not texts:
            return []
        settings = get_settings()
        if settings.mock_llm:
            self.last_embed_mock = True
            return [self._mock_embed(text) for text in texts]
        api_key = self._api_key(settings, runtime_config, purpose="embedding")
        if not api_key:
            raise RuntimeError("Embedding API key is not configured")
        self.last_embed_mock = False
        # 🛡️ 批量上限默认取配置（DashScope text-embedding-v3 上限 10），避免超限 400
        effective_batch = max(1, batch_size if batch_size is not None else settings.embedding_batch_size)
        url = self._api_base(settings, runtime_config, purpose="embedding").rstrip("/") + "/embeddings"
        out: list[list[float]] = []
        for start in range(0, len(texts), effective_batch):
            batch = texts[start : start + effective_batch]
            data = self._post_json(url, {"model": settings.openai_embedding_model, "input": batch}, api_key)
            items = sorted(data["data"], key=lambda item: item.get("index", 0))
            out.extend(item["embedding"] for item in items)
        return out

    def rerank(self, query: str, documents: list[str], *, top_n: int | None = None, model: str | None = None) -> list[dict]:
        """
        RAG 检索重排（Rerank）。

        🎯 意图与工程大局观：
            - 提供针对召回知识文档的深度语义相关性评估。向量检索侧重粗筛，而重排利用精细的 Cross-Encoder 模型做精准打分，极大缓解大模型长上下文带来的“迷失在中间（Lost in the Middle）”问题。
            
        🛡️ 防御性设计：
            - 当传入文档列表为空时，直接短路返回空列表，避免向模型网关发出空负载请求。
            - 自动兼容第三方 Rerank 厂商非标的输出格式（如 `index`/`document_index`、`relevance_score`/`rank_score` 等各种 JSON 字段变体）。
        """
        settings = get_settings()
        api_key = self._api_key(settings, purpose="rerank")
        if not documents:
            return []
        if settings.mock_llm:
            query_terms = {term.lower() for term in query.split() if term.strip()}
            ranked = []
            for index, document in enumerate(documents):
                text = document.lower()
                score = sum(1 for term in query_terms if term in text) / max(len(query_terms), 1)
                ranked.append({"index": index, "relevance_score": float(score)})
            return sorted(ranked, key=lambda item: item["relevance_score"], reverse=True)[: top_n or len(documents)]
        if not api_key:
            raise RuntimeError("Rerank API key is not configured")

        url = self._api_base(settings, purpose="rerank").rstrip("/") + "/rerank"
        payload = {
            "model": model or settings.rag_rerank_model,
            "query": query,
            "documents": documents,
            **({"top_n": top_n} if top_n else {}),
        }
        data = self._post_json(url, payload, api_key)
        results = data.get("results") or data.get("data") or []
        normalized = []
        for item in results:
            if not isinstance(item, dict):
                continue
            index = item.get("index", item.get("document_index"))
            if index is None:
                document = item.get("document")
                if document in documents:
                    index = documents.index(document)
            if index is None:
                continue
            score = item.get("relevance_score", item.get("score", item.get("rank_score", 0)))
            normalized.append({"index": int(index), "relevance_score": float(score or 0)})
        return normalized[: top_n or len(normalized)]

    # ── 私有辅助方法群 ──────────────────────────────────────────

    def _parse_chat_response(self, data: dict) -> ChatResponse:
        """
        解析并抽取模型返回的数据报文。
        
        🛡️ 防御性设计：
            - 利用极度安全的字典 `get` 级联，提供全面的降级默认值，确保就算模型返回了缺失某字段的不良报文，系统也不会产生 KeyError 级物理崩溃。
        """
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content")
        raw_tool_calls = message.get("tool_calls") or []
        # 🛡️ 兼容垫片：网关未把函数调用结构化时，从文本 content 里兜底解析回 tool_calls
        if not raw_tool_calls and isinstance(content, str) and ("<function" in content or "<tool_call>" in content):
            text_tool_calls = _extract_text_tool_calls(content)
            if text_tool_calls:
                return ChatResponse(content=None, tool_calls=text_tool_calls)
        if raw_tool_calls:
            tool_calls = []
            for tc in raw_tool_calls:
                func = tc.get("function") or {}
                tool_calls.append({
                    "id": tc.get("id") or "",
                    "type": tc.get("type") or "function",
                    "function": {
                        "name": func.get("name") or "",
                        "arguments": func.get("arguments") or "{}",
                    },
                })
            return ChatResponse(content=content or None, tool_calls=tool_calls)
        return ChatResponse(content=str(content) if content else "")

    def _api_key(self, settings, runtime_config: dict | None = None, *, purpose: str = "chat") -> str | None:
        """
        多路 API Key 路由解析器。
        
        🎯 意图与工程大局观：
            - 按优先级解析：用户私有模型 BYOK 传入密钥 -> 全局各厂商（Embedding/Rerank/DeepSeek/DashScope）专属环境变量 -> 通用 OpenAI 兼容变量，实现无感的多引擎自适应接入。
        """
        if runtime_config and purpose == "chat" and runtime_config.get("api_key"):
            return str(runtime_config["api_key"]).strip() or None
        if purpose == "embedding" and settings.embedding_api_key:
            return settings.embedding_api_key.strip() or None
        if purpose == "rerank" and settings.rerank_api_key:
            return settings.rerank_api_key.strip() or None
        if purpose == "chat":
            base = (settings.openai_api_base or "").rstrip("/")
            if settings.dashscope_api_key and base == DASHSCOPE_COMPATIBLE_BASE.rstrip("/"):
                return settings.dashscope_api_key.strip() or None
            if settings.deepseek_api_key and (base == settings.deepseek_api_base.rstrip("/") or settings.openai_model == settings.deepseek_model):
                return settings.deepseek_api_key.strip() or None
            if settings.openai_api_key:
                return settings.openai_api_key.strip() or None
            return (settings.dashscope_api_key or settings.deepseek_api_key or "").strip() or None
        return (settings.openai_api_key or settings.dashscope_api_key or "").strip() or None

    def _api_base(self, settings, runtime_config: dict | None = None, *, purpose: str = "chat") -> str:
        """
        多路 API Base URL 路由解析器。
        """
        if runtime_config and purpose == "chat" and runtime_config.get("base_url"):
            return str(runtime_config["base_url"]).strip()
        if purpose == "embedding" and settings.embedding_api_base:
            return settings.embedding_api_base
        if purpose == "rerank" and settings.rerank_api_base:
            return settings.rerank_api_base
        if purpose == "chat" and settings.deepseek_api_key and (
            (settings.openai_api_base or "").rstrip("/") == settings.deepseek_api_base.rstrip("/")
            or settings.openai_model == settings.deepseek_model
        ):
            return settings.deepseek_api_base
        base = (settings.openai_api_base or "").strip()
        if settings.dashscope_api_key and (not base or base.rstrip("/") == OPENAI_COMPATIBLE_DEFAULT_BASE.rstrip("/")):
            return DASHSCOPE_COMPATIBLE_BASE
        return base or OPENAI_COMPATIBLE_DEFAULT_BASE

    def _breaker_for(self, model_name: str):
        """
        获取指定模型的熔断器实例（惰性创建）。

        开启 circuit_breaker_distributed 且 Redis 可用时返回 Redis 共享态熔断器（多 worker 一致），
        否则用进程内三态熔断器。两者接口一致，调用方无感。
        """
        if model_name not in self._breakers:
            settings = get_settings()
            if settings.circuit_breaker_distributed:
                from core.services.rag_cache import redis_store

                if redis_store.available:
                    self._breakers[model_name] = RedisCircuitBreaker(f"model:{model_name}", failure_threshold=3, timewindow=60)
                    return self._breakers[model_name]
            self._breakers[model_name] = CircuitBreaker(failure_threshold=3, timewindow=60)
        return self._breakers[model_name]

    def _post_json(self, url: str, payload: dict, api_key: str, *, timeout_seconds: int = 60) -> dict:
        """
        同步 POST JSON 工具函数。

        🧠 魔鬼数字与前沿技术参数：
            - `timeout_seconds` 默认 60: 适合处理大模型首字延迟或较复杂的工具推理时常，防止网络波动导致提前熔断。
        
        🛡️ 防御性设计：
            - 在异常处理（urllib.error.HTTPError）中，使用 `[:800]` 截断大段的报错包体，防止大篇幅无意义的 HTML/JSON 报错直接撑爆控制台日志系统。
            - 针对底层各种网络抖动、Socket 超时、SSL 握手异常或 OS 系统层异常进行全包围式的 Try-Catch 捕获，并转化为统一的工程化 RuntimeError。
        """
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:800]
            raise RuntimeError(
                f"Model call failed: HTTP {exc.code}. Check OPENAI_API_BASE, API key and model name. {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, ssl.SSLError, OSError) as exc:
            raise RuntimeError(
                f"Model call failed: cannot connect to model gateway {url}. Check OPENAI_API_BASE, proxy, certs and API key. Raw error: {exc}"
            ) from exc

    def _post_json_stream(self, url: str, payload: dict, api_key: str) -> Iterable[dict]:
        """
        纯 Python 原生 SSE（Server-Sent Events）解析流式输出生成器（产出 chat_stream 帧协议 dict）。

        🛡️ 防御性设计：
            - `Accept` 字段指定为 "text/event-stream" 触发流式模式。
            - 过滤非数据行或空行，智能忽略并容错无法被 JSON 反序列化的碎片行。
            - 处理 EOF 状态：接收到 `[DONE]` 字符后，优雅退出流式生成，阻断无用的后续空循环读取。
        """
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "Accept": "text/event-stream",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    payload_text = line.removeprefix("data:").strip()
                    if payload_text == "[DONE]":
                        break
                    try:
                        data = json.loads(payload_text)
                    except json.JSONDecodeError:
                        continue
                    yield from self._stream_delta(data)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:800]
            raise RuntimeError(
                f"Model call failed: HTTP {exc.code}. Check OPENAI_API_BASE, API key and model name. {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, ssl.SSLError, OSError) as exc:
            raise RuntimeError(
                f"Model call failed: cannot connect to model gateway {url}. Check OPENAI_API_BASE, proxy, certs and API key. Raw error: {exc}"
            ) from exc

    def _apply_generation_limits(self, payload: dict, settings, runtime_config: dict | None, thinking: bool) -> None:
        """Apply provider-safe generation controls to chat completion payloads."""
        max_tokens = int((runtime_config or {}).get("max_tokens") or settings.llm_max_tokens or 0)
        if max_tokens > 0:
            payload["max_tokens"] = max_tokens
        if self._supports_enable_thinking(payload.get("model", ""), settings, runtime_config):
            # 🛡️ DashScope 协议约束：enable_thinking=true 仅流式调用支持，
            #    非流式请求强制关闭，避免同步路径（run/工具决策等）开思考时直接 400。
            payload["enable_thinking"] = bool(thinking) and bool(payload.get("stream"))

    def _supports_enable_thinking(self, model: str, settings, runtime_config: dict | None) -> bool:
        """Return whether the provider/model is known to accept enable_thinking."""
        base_url = self._api_base(settings, runtime_config, purpose="chat")
        marker = f"{model} {base_url} {(runtime_config or {}).get('provider', '')}".lower()
        return "qwen3" in marker

    def _stream_delta(self, data: dict) -> list[dict]:
        """
        流式消息碎片字段定位处理器（返回 0~2 个 chat_stream 协议帧）。

        🎯 双通道拆分：
            - `delta.reasoning_content`（Qwen3/DeepSeek-R1 等原生推理模型的思考流）→ reasoning 帧，
              独立通道推送，绝不与回答正文混流。
            - `delta.content` → content 帧，回答正文行为与拆分前完全一致。
            - 阶段切换的边界报文理论上可能同时携带两个字段，reasoning 帧先行保证时序正确。

        🛡️ 防御性设计：
            - 精细解析 OpenAI 的 delta 字段，兼容不同的 delta 报文返回，包括支持列表变体 `choices[0].delta.content`，避免多厂商细微差异引起的解析异常崩溃。
        """
        choices = data.get("choices") or []
        if not choices:
            return []
        first = choices[0] or {}
        frames: list[dict] = []
        delta = first.get("delta") or {}
        if isinstance(delta, dict):
            reasoning = delta.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning:
                frames.append({"type": "reasoning", "text": reasoning})
            content = delta.get("content")
            if isinstance(content, str) and content:
                frames.append({"type": "content", "text": content})
                return frames
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        parts.append(item["text"])
                if "".join(parts):
                    frames.append({"type": "content", "text": "".join(parts)})
                return frames
            if frames:
                return frames
        message = first.get("message") or {}
        if isinstance(message, dict) and isinstance(message.get("content"), str) and message["content"]:
            return [{"type": "content", "text": message["content"]}]
        text = first.get("text")
        if isinstance(text, str) and text:
            return [{"type": "content", "text": text}]
        return []

    def _content_text(self, content) -> str:
        """
        标准/多模态消息包体文本化抽取。
        
        🛡️ 防御性设计：
            - 智能抹平字符串与 OpenAI 多模态列表结构（List of dicts containing image_url and text）的格式差异，保障系统在遇到多模态输入时能稳定降级提取用于哈希或元数据分析的文本。
        """
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif isinstance(item, dict) and item.get("type") == "image_url":
                    parts.append("[image]")
            return " ".join(parts)
        return str(content)
