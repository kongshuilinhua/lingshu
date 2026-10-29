"""
会话记忆摘要服务。

🎯 摘要压缩 + 加载装配逻辑，纯函数实现。

模式：
  - parse_memory → 兼容解析三种历史格式，纯函数
  - build_memory_payload → 合并当前轮、超阈值压缩、降级保旧，纯函数（summarizer 注入）
  - summarize_turns → LLM 调用 + 异常降级，仿 query_understanding.analyze 模式
"""
from __future__ import annotations

import json
import logging

from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
try:
    from langchain_core.messages.utils import trim_messages, count_tokens_approximately
except ImportError:
    def count_tokens_approximately(msgs):
        return sum(len(m.content) for m in msgs) // 2

    def trim_messages(messages, max_tokens, strategy, token_counter, start_on):
        keep = []
        tokens = 0
        for msg in reversed(messages):
            t = token_counter([msg])
            if tokens + t <= max_tokens:
                keep.append(msg)
                tokens += t
            else:
                break
        keep.reverse()
        if start_on == "human" and keep and not isinstance(keep[0], HumanMessage):
            keep = keep[1:]
        return keep

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from core.integrations.langchain_provider import get_chat_model
from langsmith import traceable

logger = logging.getLogger(__name__)

# ── 解析 ─────────────────────────────────────────────────────────────


def parse_memory(raw: str) -> tuple[str, list[dict]]:
    """
    兼容解析旧 summary：新 dict{summary,turns} / 旧 list[turn] / 旧 \\n===\\n 文本。
    返回 (summary, turns)。失败返回 ('', [])。

    🎯 解析 DB 中的消息列表 + summary 记录，装配为结构化数据。
    """
    if not raw or not raw.strip():
        return "", []

    trimmed = raw.strip()

    # 1) 尝试 JSON 解析
    try:
        data = json.loads(trimmed)
    except (json.JSONDecodeError, ValueError):
        data = None

    if isinstance(data, dict):
        # 新格式: {"summary": "...", "turns": [...]}
        summary = str(data.get("summary") or "")
        turns = data.get("turns")
        if isinstance(turns, list):
            return summary, [t for t in turns if isinstance(t, dict) and "user" in t and "assistant" in t]
        return "", []

    if isinstance(data, list):
        # 旧格式: [{"user": ..., "assistant": ...}, ...]
        turns = [t for t in data if isinstance(t, dict) and "user" in t and "assistant" in t]
        return "", turns

    # 2) 旧版 \\n===\\n 分隔文本
    return "", _parse_separator_text(trimmed)


def _parse_separator_text(raw: str) -> list[dict]:
    """解析旧版 \\n===\\n 分隔的会话文本。对照现有 _update_session_memory 兼容逻辑。"""
    turns = []
    for turn_text in raw.split("\n===\n"):
        turn_text = turn_text.strip()
        if not turn_text:
            continue
        if "助手：" in turn_text:
            parts = turn_text.split("助手：", 1)
            u_part = parts[0].replace("用户：", "").strip()
            a_part = parts[1].strip()
            if u_part or a_part:
                turns.append({"user": u_part, "assistant": a_part})
    return turns


# ── 核心载荷构建（纯函数）────────────────────────────────────────────


def count_str_tokens(text: str) -> int:
    return count_tokens_approximately([HumanMessage(content=text)])


def soft_truncate(text: str, target_tokens: int) -> str:
    current_tokens = count_str_tokens(text)
    if current_tokens <= target_tokens:
        return text

    ratio = target_tokens / current_tokens
    target_len = int(len(text) * ratio)
    if target_len <= 0:
        return "...(截断)"

    boundaries = ['\n', '。', '.', '？', '?', '！', '!', '；', ';']
    best_idx = -1
    lookback = max(100, int(target_len * 0.2))
    search_start = max(0, target_len - lookback)
    for i in range(target_len - 1, search_start - 1, -1):
        if text[i] in boundaries:
            best_idx = i
            break

    if best_idx != -1:
        return text[:best_idx + 1] + "...(截断)"
    else:
        return text[:target_len] + "...(截断)"


def truncate_recent_turns(turns: list[dict], recent_token_budget: int | None = None) -> list[dict]:
    if recent_token_budget is None:
        recent_token_budget = 1500
    flat_msgs = []
    for idx, turn in enumerate(turns):
        if "user" in turn:
            flat_msgs.append({"turn_idx": idx, "role": "user", "text": turn["user"]})
        if "assistant" in turn:
            flat_msgs.append({"turn_idx": idx, "role": "assistant", "text": turn["assistant"]})

    lc_msgs = []
    for m in flat_msgs:
        if m["role"] == "user":
            lc_msgs.append(HumanMessage(content=m["text"]))
        else:
            lc_msgs.append(AIMessage(content=m["text"]))

    total_tokens = count_tokens_approximately(lc_msgs)
    excess = total_tokens - recent_token_budget
    if excess <= 0:
        return turns

    for m in flat_msgs:
        if excess <= 0:
            break
        text = m["text"]
        curr_tokens = count_str_tokens(text)
        min_keep = 150
        if curr_tokens <= min_keep:
            continue

        target_tokens = max(min_keep, curr_tokens - excess)
        truncated_text = soft_truncate(text, target_tokens)
        reduced_tokens = curr_tokens - count_str_tokens(truncated_text)

        m["text"] = truncated_text
        excess -= reduced_tokens

    new_turns = [dict(t) for t in turns]
    for m in flat_msgs:
        new_turns[m["turn_idx"]][m["role"]] = m["text"]

    return new_turns


def build_memory_payload(
    raw: str,
    new_turn: dict,
    *,
    token_budget: int | None = None,
    recent_token_budget: int | None = None,
    max_turns: int | None = None,
    keep_recent: int | None = None,
    summarizer,
) -> dict:
    """
    合并当前轮、超阈值则把较旧 turns 交 summarizer 压缩。
    """
    summary, turns = parse_memory(raw)
    turns = turns + [new_turn]

    lc_msgs = []
    msg_metadata = []
    for idx, t in enumerate(turns):
        u = t.get("user") or ""
        a = t.get("assistant") or ""
        if u:
            lc_msgs.append(HumanMessage(content=u))
            msg_metadata.append((idx, "user"))
        if a:
            lc_msgs.append(AIMessage(content=a))
            msg_metadata.append((idx, "assistant"))

    if (token_budget is None or token_budget <= 0) and max_turns is not None:
        if len(turns) <= max_turns:
            payload = {"summary": summary, "turns": turns}
            payload["turns"] = truncate_recent_turns(payload["turns"], recent_token_budget)
            return payload
        older_turns = turns[:-keep_recent]
        recent_turns = turns[-keep_recent:]
    else:
        total_tokens = count_tokens_approximately(lc_msgs)
        if total_tokens <= token_budget:
            payload = {"summary": summary, "turns": turns}
            payload["turns"] = truncate_recent_turns(payload["turns"], recent_token_budget)
            return payload

        trimmed_msgs = trim_messages(
            lc_msgs,
            max_tokens=recent_token_budget,
            strategy="last",
            token_counter=count_tokens_approximately,
            start_on="human",
        )

        if not trimmed_msgs:
            older_turns = turns
            recent_turns = []
        else:
            num_trimmed = len(lc_msgs) - len(trimmed_msgs)
            first_kept_turn_idx = msg_metadata[num_trimmed][0]
            older_turns = turns[:first_kept_turn_idx]
            recent_turns = turns[first_kept_turn_idx:]

    try:
        new_summary = summarizer(older_turns, summary) or summary
    except Exception:
        logger.warning("记忆摘要生成失败，降级保留旧摘要。", exc_info=True)
        new_summary = summary

    payload = {"summary": new_summary, "turns": recent_turns}
    payload["turns"] = truncate_recent_turns(payload["turns"], recent_token_budget)
    return payload


# ── LLM 摘要器 ────────────────────────────────────────────────────────


def _build_summary_messages(
    older_turns: list[dict],
    existing_summary: str,
    *,
    max_chars: int,
) -> list[dict]:
    """
    构造摘要 LLM 调用的消息列表。

    🎯 system prompt → 历史摘要（如有，作为 assistant 消息）→ 待压缩 turns → user 指令。
    """
    system = (
        "你是一个对话摘要压缩器。把以下多轮对话压成简洁的中文要点摘要，"
        "保留事实、决定、用户偏好。若已有历史摘要，则在此基础上合并去重、更新补充，"
        "不得将历史摘要当作新增事实来源，若与本轮冲突以本轮为准。"
        f"输出一段紧凑的摘要文本（严格≤{max_chars}字符），不要分段，不要列出轮次。"
    )
    messages: list[dict] = [{"role": "system", "content": system}]

    if existing_summary.strip():
        messages.append({
            "role": "assistant",
            "content": (
                "历史摘要（仅用于合并去重，不得作为事实新增来源；若与本轮对话冲突，以本轮对话为准）：\n"
                + existing_summary.strip()
            ),
        })

    # 把 older_turns 逐条转为 user/assistant 消息
    for turn in older_turns:
        user_text = (turn.get("user") or "").strip()
        assistant_text = (turn.get("assistant") or "").strip()
        if user_text:
            messages.append({"role": "user", "content": user_text})
        if assistant_text:
            messages.append({"role": "assistant", "content": assistant_text})

    messages.append({
        "role": "user",
        "content": (
            f"合并以上对话与历史摘要，去重后输出更新摘要。要求：严格≤{max_chars}字符；仅一行。"
        ),
    })
    return messages


@traceable(name="memory.compact")
def summarize_turns(
    provider,
    *,
    older_turns: list[dict],
    existing_summary: str,
    config: dict,
    runtime_config: dict | None = None,
) -> str:
    """
    调用 LLM 对较旧对话轮次生成摘要。

    🎯 严格仿 core/services/query_understanding.py:analyze 的模式：
        prompt | get_chat_model(...) | StrOutputParser()
        任何异常都降级保留 existing_summary，绝不抛、绝不丢摘要。
    """
    if not config.get("enabled", True) or not older_turns:
        return existing_summary

    try:
        max_chars = int(config.get("summary_max_chars", 800))
        messages = _build_summary_messages(
            older_turns,
            existing_summary,
            max_chars=max_chars,
        )

        lc_prompt_messages = []
        for m in messages:
            role = m["role"]
            content = m["content"]
            if role == "system":
                lc_prompt_messages.append(SystemMessage(content=content))
            elif role == "assistant":
                lc_prompt_messages.append(AIMessage(content=content))
            else:
                lc_prompt_messages.append(HumanMessage(content=content))

        prompt = ChatPromptTemplate.from_messages(lc_prompt_messages)
        model_name = config.get("model")
        chat_model = get_chat_model(
            model=model_name,
            temperature=0.0,
            runtime_config=runtime_config,
        )

        chain = prompt | chat_model | StrOutputParser()
        text = chain.invoke({})
        text = (text or "").strip()
        if not text:
            return existing_summary
        return text[:max_chars] if len(text) > max_chars else text
    except Exception:
        logger.warning("对话记忆摘要 LLM 调用失败，降级保留旧摘要。", exc_info=True)
        return existing_summary
