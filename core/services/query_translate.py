"""跨语言查询翻译：中文查询 → 英文翻译，供 BM25 检索英文文档。

🎯 设计意图：
    知识库文档是英文的，用户用中文提问时，jieba 切出的中文词项与英文文档词项
    几乎零重叠，BM25 路在跨语言对上基本哑火（BGE-M3 论文 MKQA 基准：sparse
    zh_cn 仅 35.4 vs dense 74.6）。本模块在检索前把中文查询翻译成英文，让 BM25
    回归单语英文有效检索，恢复对 API 名 / 版本号 / 错误码等精确词项的召回。

    Dense 路（bge-m3 多语言 embedding）和 Rerank 路（bge-reranker-v2-m3）不受
    影响——它们自身支持跨语言，继续用中文原查询。

🛡️ 防御性设计：
    任何异常都返回 None（降级为用原始查询），绝不抛出、绝不阻断检索主流程。
"""

from __future__ import annotations

import hashlib

from core.config import get_settings

# 翻译系统提示词：保留技术专有名词原文，只输出译文
_SYSTEM_PROMPT = (
    "You are a query translation assistant for a RAG system. "
    "Translate the user's Chinese query into English for searching English technical documentation.\n"
    "Rules:\n"
    "1. Output ONLY the English translation, no explanations.\n"
    "2. Preserve technical terms in their original English form "
    "(e.g. RAG, embedding, ReAct, FastAPI, Milvus, Redis, BM25, HNSW, SSE, JWT, SSRF).\n"
    "3. Preserve API names, function names, version numbers, and error codes as-is.\n"
    "4. Keep the query's original intent — do not expand or narrow it."
)


def _is_mostly_ascii(text: str) -> bool:
    """判断文本是否以 ASCII（英文）为主——超过 70% ASCII 字符则跳过翻译。"""
    if not text:
        return True
    ascii_count = sum(1 for c in text if ord(c) < 128)
    return ascii_count / len(text) > 0.7


# 手动进程级缓存：{(query_hash, model): translated_text}
_TRANSLATE_CACHE: dict[tuple[str, str | None], str] = {}
_CACHE_MAX = 1000


def translate_query(provider, query: str, *, runtime_config: dict | None = None) -> str | None:
    """把中文查询翻译成英文，供 BM25 检索英文文档。

    返回 None 表示不需要翻译或翻译失败——调用方应回退到原始查询。
    永不抛出异常。

    Args:
        provider: OpenAICompatibleProvider 实例（复用项目的 LLM 网关）
        query: 用户查询文本（通常是中文）
        runtime_config: BYOK 运行时配置（透传给 provider.chat）

    Returns:
        英文翻译文本，或 None（跳过/失败）
    """
    settings = get_settings()

    # 功能总开关
    if not settings.translate_query_enabled:
        return None

    # Mock LLM 模式：翻译结果不可信，跳过
    if settings.mock_llm:
        return None

    # 已是英文为主，不需要翻译
    if _is_mostly_ascii(query):
        return None

    # 空查询
    if not query.strip():
        return None

    # 缓存命中
    model = settings.translate_model
    cache_key = (hashlib.sha256(query.encode()).hexdigest()[:16], model)
    cached = _TRANSLATE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    # 调用 LLM 翻译
    try:
        resp = provider.chat(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            model=model,
            temperature=0.0,
            runtime_config=runtime_config,
        )
        translated = (resp.content or "").strip()
        # 简单有效性检查：非空、不等于原文、不超过原文 5 倍长度
        if translated and translated != query and len(translated) < len(query) * 5:
            # 写入缓存（超限清空）
            if len(_TRANSLATE_CACHE) >= _CACHE_MAX:
                _TRANSLATE_CACHE.clear()
            _TRANSLATE_CACHE[cache_key] = translated
            return translated
    except Exception:
        pass

    return None
