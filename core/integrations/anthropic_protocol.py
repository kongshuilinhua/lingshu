"""Translate the runtime's messages into the Anthropic Messages protocol."""

from __future__ import annotations

import base64
import copy
import json
import urllib.parse

ANTHROPIC_VERSION = "2023-06-01"


def endpoint(base_url: str, resource: str) -> str:
    base = base_url.rstrip("/")
    return f"{base}/{resource}" if base.endswith("/v1") else f"{base}/v1/{resource}"


def headers(api_key: str) -> dict:
    return {"Content-Type": "application/json", "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION}


def content_blocks(content) -> list[dict]:
    if content is None or content == "":
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        raise ValueError("Unsupported Anthropic message content")
    blocks = []
    for part in content:
        kind = part.get("type")
        if kind == "text":
            if part.get("text"):
                blocks.append({"type": "text", "text": str(part["text"])})
        elif kind == "image_url":
            url = part.get("image_url", {}).get("url", "")
            if url.startswith("data:"):
                metadata, separator, data = url.partition(",")
                mime = metadata.removeprefix("data:").removesuffix(";base64")
                if not separator or not metadata.endswith(";base64") or mime not in {
                    "image/jpeg", "image/png", "image/gif", "image/webp",
                }:
                    raise ValueError("Unsupported Anthropic image data")
                try:
                    base64.b64decode(data, validate=True)
                except ValueError as exc:
                    raise ValueError("Invalid Anthropic image data") from exc
                source = {"type": "base64", "media_type": mime, "data": data}
            else:
                parsed = urllib.parse.urlsplit(url)
                if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError("Anthropic image URL must use HTTPS")
                source = {"type": "url", "url": url}
            blocks.append({"type": "image", "source": source})
        else:
            raise ValueError("Unsupported Anthropic content block")
    return blocks


def tool_choice(choice) -> dict:
    if choice is None or choice == "auto":
        return {"type": "auto"}
    if choice in ("required", "any"):
        return {"type": "any"}
    if choice == "none":
        return {"type": "none"}
    if isinstance(choice, str):
        return {"type": "tool", "name": choice}
    if isinstance(choice, dict) and choice.get("type") == "function":
        return {"type": "tool", "name": choice["function"]["name"]}
    raise ValueError("Unsupported tool choice")


def request_body(messages: list[dict], *, model: str, max_tokens: int,
                 stream: bool = False, tools: list[dict] | None = None,
                 choice=None, thinking: bool = False) -> dict:
    turns = []
    system = []
    for message in messages:
        role = message.get("role")
        if role in {"system", "developer"}:
            system.extend(content_blocks(message.get("content")))
            continue
        if role == "tool":
            role = "user"
            blocks = [{"type": "tool_result", "tool_use_id": message["tool_call_id"],
                       "content": content_blocks(message.get("content"))}]
        elif role == "assistant" and message.get("anthropic_content"):
            # Thinking signatures and tool_use blocks must be replayed unchanged.
            blocks = copy.deepcopy(message["anthropic_content"])
        elif role in {"user", "assistant"}:
            blocks = content_blocks(message.get("content"))
            for call in message.get("tool_calls") or []:
                function = call["function"]
                arguments = function.get("arguments") or "{}"
                arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be an object")
                blocks.append({"type": "tool_use", "id": call["id"],
                               "name": function["name"], "input": arguments})
        else:
            raise ValueError("Unsupported Anthropic message role")
        if not blocks:
            continue
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].extend(blocks)
        else:
            turns.append({"role": role, "content": blocks})
    if not turns or turns[0]["role"] != "user":
        raise ValueError("Anthropic messages must start with a user turn")
    if max_tokens <= 0:
        raise ValueError("Anthropic max_tokens must be positive")
    body = {"model": model, "messages": turns, "max_tokens": max_tokens, "stream": stream}
    if system:
        body["system"] = system
    if tools:
        body["tools"] = [{"name": tool["function"]["name"],
                          "description": tool["function"].get("description", ""),
                          "input_schema": tool["function"].get("parameters") or {"type": "object", "properties": {}}}
                         for tool in tools]
        body["tool_choice"] = tool_choice(choice)
    # Native Messages uses the vendor's default temperature. Newer Claude models
    # reject temperature overrides; enabling thinking also requires its default.
    if thinking:
        body["thinking"] = {"type": "adaptive"}
    return body


def normalized_tool(block: dict) -> dict:
    arguments = block.get("input")
    if not isinstance(arguments, dict) or not block.get("id") or not block.get("name"):
        raise RuntimeError("Model returned an invalid tool call")
    return {"id": block["id"], "type": "function", "function": {
        "name": block["name"], "arguments": json.dumps(arguments, ensure_ascii=False)}}


def parse_response(data: dict) -> dict:
    blocks = data.get("content")
    if not isinstance(blocks, list):
        raise RuntimeError("Model returned an invalid Anthropic response")
    calls = [normalized_tool(block) for block in blocks if block.get("type") == "tool_use"]
    return {"content": "".join(block.get("text", "") for block in blocks if block.get("type") == "text"),
            "tool_calls": calls or None, "provider_content": copy.deepcopy(blocks)}


def stream_frames(response):
    """Consume native SSE, including split JSON tool arguments and error events."""
    pending = []
    blocks = {}
    completed = False
    for raw_line in response:
        line = raw_line.decode("utf-8").rstrip("\r\n")
        if line.startswith("data:"):
            pending.append(line[5:].lstrip())
            if sum(map(len, pending)) > 1_000_000:
                raise RuntimeError("Model stream event is too large")
            continue
        if line or not pending:
            continue
        data = json.loads("\n".join(pending))
        pending = []
        kind = data.get("type")
        if kind == "error":
            # Provider-controlled text can contain secrets; expose only its type.
            code = (data.get("error") or {}).get("type")
            if code not in {"invalid_request_error", "authentication_error", "permission_error", "not_found_error",
                            "rate_limit_error", "api_error", "overloaded_error"}:
                code = "unknown"
            raise RuntimeError("Model call failed: Anthropic stream error " + code)
        if kind == "content_block_start":
            index = data["index"]
            if len(blocks) >= 500:
                raise RuntimeError("Model stream has too many blocks")
            blocks[index] = {"block": copy.deepcopy(data["content_block"]), "json": ""}
            block = blocks[index]["block"]
            if block.get("type") == "text" and block.get("text"):
                yield {"type": "content", "text": block["text"]}
        elif kind == "content_block_delta":
            delta = data.get("delta") or {}
            if delta.get("type") == "text_delta":
                yield {"type": "content", "text": delta.get("text", "")}
            elif delta.get("type") == "thinking_delta":
                yield {"type": "reasoning", "text": delta.get("thinking", "")}
            elif delta.get("type") == "input_json_delta":
                state = blocks[data["index"]]
                state["json"] += delta.get("partial_json", "")
                if len(state["json"]) > 1_000_000:
                    raise RuntimeError("Model tool arguments are too large")
        elif kind == "content_block_stop":
            state = blocks.get(data["index"])
            if state and state["block"].get("type") == "tool_use":
                if state["json"]:
                    state["block"]["input"] = json.loads(state["json"])
                yield {"type": "tool_call", "tool_call": normalized_tool(state["block"])}
        elif kind == "message_stop":
            completed = True
            break
    if not completed:
        raise RuntimeError("Model call failed: incomplete Anthropic stream")
