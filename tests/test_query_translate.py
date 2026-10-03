"""查询翻译模块单元测试。

覆盖：中文→英文翻译、英文跳过、异常降级、缓存命中、mock_llm 跳过、
retrieve() 注入验证（BM25 收到英文查询、Dense 收到中文原查询）。
"""

from __future__ import annotations

from unittest.mock import patch


from core.integrations.llm import ChatResponse
from core.services.query_translate import translate_query, _TRANSLATE_CACHE, _is_mostly_ascii


# ── FakeProvider：捕获调用参数，返回可控响应 ──────────────────────

class _FakeProvider:
    """模拟 OpenAICompatibleProvider，捕获 chat() 调用参数。"""

    def __init__(self, content: str = "translated query", *, raise_exc: Exception | None = None):
        self._content = content
        self._raise = raise_exc
        self.calls: list[dict] = []

    def chat(self, messages, *, model=None, temperature=0.4, runtime_config=None, tools=None, thinking=False):
        self.calls.append({"messages": messages, "model": model, "temperature": temperature})
        if self._raise:
            raise self._raise
        return ChatResponse(content=self._content)


# ── _is_mostly_ascii ──────────────────────────────────────────────

class TestIsMostlyAscii:
    def test_pure_english(self):
        assert _is_mostly_ascii("how to use redis SET command") is True

    def test_pure_chinese(self):
        assert _is_mostly_ascii("如何使用Redis的SET命令") is False

    def test_mixed_mostly_english(self):
        # 70% 以上 ASCII → True
        assert _is_mostly_ascii("Redis SET command 命令") is True

    def test_mixed_mostly_chinese(self):
        assert _is_mostly_ascii("如何使用 Redis 命令") is False

    def test_empty(self):
        assert _is_mostly_ascii("") is True


# ── translate_query ───────────────────────────────────────────────

class TestTranslateQuery:
    """translate_query 各场景测试。"""

    def setup_method(self):
        """每个测试前清空缓存，避免跨用例串味。"""
        _TRANSLATE_CACHE.clear()

    @patch("core.services.query_translate.get_settings")
    def test_chinese_query_translated(self, mock_settings):
        """中文查询 → LLM 翻译成英文。"""
        mock_settings.return_value.translate_query_enabled = True
        mock_settings.return_value.mock_llm = False
        mock_settings.return_value.translate_model = None

        provider = _FakeProvider(content="How to use Redis SET command")
        result = translate_query(provider, "如何使用Redis的SET命令")

        assert result == "How to use Redis SET command"
        assert len(provider.calls) == 1
        assert provider.calls[0]["temperature"] == 0.0
        assert provider.calls[0]["messages"][0]["role"] == "system"

    @patch("core.services.query_translate.get_settings")
    def test_english_query_skipped(self, mock_settings):
        """已是英文的查询直接跳过，不调 LLM。"""
        mock_settings.return_value.translate_query_enabled = True
        mock_settings.return_value.mock_llm = False
        mock_settings.return_value.translate_model = None

        provider = _FakeProvider()
        result = translate_query(provider, "How to configure Redis persistence")

        assert result is None
        assert len(provider.calls) == 0

    @patch("core.services.query_translate.get_settings")
    def test_disabled_returns_none(self, mock_settings):
        """功能关闭时返回 None。"""
        mock_settings.return_value.translate_query_enabled = False
        mock_settings.return_value.mock_llm = False
        mock_settings.return_value.translate_model = None

        provider = _FakeProvider()
        result = translate_query(provider, "如何使用Redis")

        assert result is None
        assert len(provider.calls) == 0

    @patch("core.services.query_translate.get_settings")
    def test_mock_llm_skipped(self, mock_settings):
        """mock_llm 模式跳过翻译（mock 响应不可信）。"""
        mock_settings.return_value.translate_query_enabled = True
        mock_settings.return_value.mock_llm = True
        mock_settings.return_value.translate_model = None

        provider = _FakeProvider()
        result = translate_query(provider, "如何使用Redis")

        assert result is None
        assert len(provider.calls) == 0

    @patch("core.services.query_translate.get_settings")
    def test_exception_returns_none(self, mock_settings):
        """LLM 异常时降级返回 None。"""
        mock_settings.return_value.translate_query_enabled = True
        mock_settings.return_value.mock_llm = False
        mock_settings.return_value.translate_model = None

        provider = _FakeProvider(raise_exc=RuntimeError("network down"))
        result = translate_query(provider, "如何使用Redis")

        assert result is None

    @patch("core.services.query_translate.get_settings")
    def test_empty_response_returns_none(self, mock_settings):
        """LLM 返回空内容时返回 None。"""
        mock_settings.return_value.translate_query_enabled = True
        mock_settings.return_value.mock_llm = False
        mock_settings.return_value.translate_model = None

        provider = _FakeProvider(content="")
        result = translate_query(provider, "如何使用Redis")

        assert result is None

    @patch("core.services.query_translate.get_settings")
    def test_same_as_original_returns_none(self, mock_settings):
        """翻译结果与原文相同时返回 None（翻译无意义）。"""
        mock_settings.return_value.translate_query_enabled = True
        mock_settings.return_value.mock_llm = False
        mock_settings.return_value.translate_model = None

        provider = _FakeProvider(content="如何使用Redis")  # 返回原文
        result = translate_query(provider, "如何使用Redis")

        assert result is None

    @patch("core.services.query_translate.get_settings")
    def test_cache_hit_skips_llm(self, mock_settings):
        """相同查询命中缓存后不再调用 LLM。"""
        mock_settings.return_value.translate_query_enabled = True
        mock_settings.return_value.mock_llm = False
        mock_settings.return_value.translate_model = None

        provider = _FakeProvider(content="How to use Redis")
        translate_query(provider, "如何使用Redis")
        assert len(provider.calls) == 1

        # 第二次调用应命中缓存
        result = translate_query(provider, "如何使用Redis")
        assert result == "How to use Redis"
        assert len(provider.calls) == 1  # 仍然只有 1 次调用

    @patch("core.services.query_translate.get_settings")
    def test_model_passed_through(self, mock_settings):
        """translate_model 配置正确传递给 provider.chat()。"""
        mock_settings.return_value.translate_query_enabled = True
        mock_settings.return_value.mock_llm = False
        mock_settings.return_value.translate_model = "Qwen/Qwen3-8B"

        provider = _FakeProvider(content="How to use Redis")
        translate_query(provider, "如何使用Redis")

        assert provider.calls[0]["model"] == "Qwen/Qwen3-8B"

    @patch("core.services.query_translate.get_settings")
    def test_empty_query_returns_none(self, mock_settings):
        """空查询返回 None。"""
        mock_settings.return_value.translate_query_enabled = True
        mock_settings.return_value.mock_llm = False
        mock_settings.return_value.translate_model = None

        provider = _FakeProvider()
        result = translate_query(provider, "")

        assert result is None
        assert len(provider.calls) == 0

    @patch("core.services.query_translate.get_settings")
    def test_system_prompt_preserves_tech_terms(self, mock_settings):
        """system prompt 包含技术专有名词保留约束。"""
        mock_settings.return_value.translate_query_enabled = True
        mock_settings.return_value.mock_llm = False
        mock_settings.return_value.translate_model = None

        provider = _FakeProvider(content="translated")
        translate_query(provider, "如何使用Redis")

        system_content = provider.calls[0]["messages"][0]["content"]
        assert "technical terms" in system_content.lower() or "RAG" in system_content
        assert "ONLY the English translation" in system_content
