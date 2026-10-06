"""Use the same native gateway for LangChain memory and retrieval helpers."""

import json

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from core.integrations.llm import OpenAICompatibleProvider


class AnthropicChatModel(BaseChatModel):
    model_name: str
    runtime_config: dict = Field(exclude=True, repr=False)

    @property
    def _llm_type(self) -> str:
        return "lingshu-anthropic"

    @property
    def _identifying_params(self) -> dict:
        return {"model_name": self.model_name, "provider": "anthropic"}

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self.bind(tools=[convert_to_openai_tool(tool) for tool in tools], tool_choice=tool_choice, **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        turns = []
        for message in messages:
            role = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}.get(message.type)
            if not role:
                raise ValueError("Unsupported message role")
            turn = {"role": role, "content": message.content}
            if isinstance(message, ToolMessage):
                turn["tool_call_id"] = message.tool_call_id
            if isinstance(message, AIMessage):
                if message.additional_kwargs.get("anthropic_content"):
                    turn["anthropic_content"] = message.additional_kwargs["anthropic_content"]
                if message.tool_calls:
                    turn["tool_calls"] = [{"id": call["id"], "type": "function", "function": {
                        "name": call["name"], "arguments": json.dumps(call["args"])}} for call in message.tool_calls]
            turns.append(turn)
        runtime = dict(self.runtime_config)
        if kwargs.get("max_tokens"):
            runtime["max_tokens"] = kwargs["max_tokens"]
        response = OpenAICompatibleProvider().chat(turns, model=self.model_name, runtime_config=runtime,
            tools=kwargs.get("tools"), tool_choice=kwargs.get("tool_choice"))
        tool_calls = [{"name": call["function"]["name"], "args": json.loads(call["function"]["arguments"]),
                       "id": call["id"], "type": "tool_call"} for call in response.tool_calls or []]
        output = AIMessage(content=response.content or "", tool_calls=tool_calls,
            additional_kwargs={"anthropic_content": response.provider_content} if response.provider_content else {})
        return ChatResult(generations=[ChatGeneration(message=output)])
