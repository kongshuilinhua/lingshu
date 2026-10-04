from __future__ import annotations

from typing import Any, List, Optional
from langchain_core.language_models.chat_models import SimpleChatModel
from langchain_core.messages import BaseMessage
from langchain_core.callbacks.manager import CallbackManagerForLLMRun


class MockChatOpenAI(SimpleChatModel):
    model: str = "mock-model"

    def _call(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> str:
        # Return a mock summary/content
        user_text = ""
        for msg in reversed(messages):
            if msg.type == "human":
                user_text = msg.content
                break
        return f"Mock answer/summary for: {user_text[:50]}"

    def with_structured_output(self, schema, **kwargs):
        class FakeStructuredRunnable:
            def __init__(self, schema):
                self.schema = schema
            def invoke(self, input, config=None):
                if hasattr(self.schema, "model_fields"):
                    data = {}
                    for name, field in self.schema.model_fields.items():
                        if name == "facts":
                            data[name] = ["Mocked extracted fact 1", "Mocked extracted fact 2"]
                        elif name == "preferences":
                            data[name] = {"language": "zh-CN"}
                        else:
                            data[name] = "mock_value"
                    return self.schema(**data)
                return {}
        return FakeStructuredRunnable(schema)

    @property
    def _llm_type(self) -> str:
        return "mock-chat-openai"


def get_chat_model(*, model=None, temperature=0.0, runtime_config=None, **kwargs):
    """返回与原生 OpenAICompatibleProvider 指向相同端点的 ChatOpenAI。"""
    from core.config import get_settings
    settings = get_settings()

    if settings.mock_llm:
        resolved_model = model or (runtime_config or {}).get("chat_model") or settings.openai_model
        return MockChatOpenAI(model=resolved_model)

    import httpx
    from langchain_openai import ChatOpenAI
    from core.integrations.llm import OpenAICompatibleProvider
    from core.security.outbound_http import PublicHTTPSHttpxTransport

    provider = OpenAICompatibleProvider()
    api_key = provider._api_key(settings, runtime_config, purpose="chat")
    base_url = provider._api_base(settings, runtime_config, purpose="chat")
    resolved_model = model or (runtime_config or {}).get("chat_model") or settings.openai_model
    if (runtime_config or {}).get("untrusted_base_url"):
        kwargs["http_client"] = httpx.Client(transport=PublicHTTPSHttpxTransport(), follow_redirects=False)
    return ChatOpenAI(
        model=resolved_model,
        base_url=base_url,
        api_key=api_key or "x",
        temperature=temperature,
        **kwargs,
    )
