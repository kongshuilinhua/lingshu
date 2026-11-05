from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from core.db.models import AgentMemoryProfile

# 🧠 魔鬼数字限制：
# 长效记忆摘要字符上限 4000 字，事实条目限制最多 50 条。
# 意图说明：长效记忆如果过长，在被拼入 System Prompt 后会喧宾夺主，挤占大模型宝贵的上下文空间，因此必须在此前置截断。
MAX_MEMORY_SUMMARY_CHARS = 4000
MAX_MEMORY_FACTS = 50


def default_memory_profile_payload(agent_id: int) -> dict:
    """默认空的长效记忆传输载荷（DTO）。"""
    return {
        "agent_id": agent_id,
        "enabled": False,
        "summary": "",
        "facts": [],
        "preferences": {},
        "updated_at": None,
    }


def memory_profile_payload(profile: AgentMemoryProfile | None, *, agent_id: int | None = None) -> dict:
    """将 ORM Profile 转换为标准的 API 传输 Payload，当不存在 profile 时返回预设默认空格式。"""
    if not profile:
        if agent_id is None:
            raise ValueError("agent_id is required for a default memory profile payload")
        return default_memory_profile_payload(agent_id)
    return {
        "agent_id": profile.agent_id,
        "enabled": bool(profile.enabled),
        "summary": profile.summary or "",
        "facts": normalize_facts(profile.facts),
        "preferences": normalize_preferences(profile.preferences),
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }


def get_memory_profile(
    db: Session,
    *,
    workspace_id: int,
    user_id: int,
    agent_id: int,
) -> AgentMemoryProfile | None:
    """
    租户隔离级拉取长效记忆档案。
    🛡️ 水平越权防线：强制绑定 `workspace_id` 与 `user_id`，防止跨用户非法读取记忆。
    """
    return (
        db.query(AgentMemoryProfile)
        .filter(
            AgentMemoryProfile.workspace_id == workspace_id,
            AgentMemoryProfile.user_id == user_id,
            AgentMemoryProfile.agent_id == agent_id,
        )
        .first()
    )


def upsert_memory_profile(
    db: Session,
    *,
    workspace_id: int,
    user_id: int,
    agent_id: int,
    payload: dict,
) -> AgentMemoryProfile:
    """
    更新或创建长效记忆档案。
    """
    profile = get_memory_profile(db, workspace_id=workspace_id, user_id=user_id, agent_id=agent_id)
    if not profile:
        profile = AgentMemoryProfile(
            workspace_id=workspace_id,
            user_id=user_id,
            agent_id=agent_id,
        )
        db.add(profile)

    if "enabled" in payload:
        profile.enabled = bool(payload["enabled"])
    if "summary" in payload:
        profile.summary = normalize_summary(payload["summary"])
    if "facts" in payload:
        profile.facts = normalize_facts(payload["facts"])
    if "preferences" in payload:
        profile.preferences = normalize_preferences(payload["preferences"])
    profile.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(profile)
    return profile


def delete_memory_profile(
    db: Session,
    *,
    workspace_id: int,
    user_id: int,
    agent_id: int,
) -> bool:
    """
    彻底物理擦除记忆档案。
    """
    profile = get_memory_profile(db, workspace_id=workspace_id, user_id=user_id, agent_id=agent_id)
    if not profile:
        return False
    db.delete(profile)
    db.commit()
    return True


def memory_used_event(
    profile: AgentMemoryProfile | None,
    *,
    session_summary_used: bool,
    recalled_facts: list[dict] | None = None,
) -> dict:
    """生成高级 Trace 看板中关于“长短期记忆使用指标”的事件包。"""
    facts = normalize_facts(profile.facts) if profile and profile.enabled else []
    recalled = recalled_facts if recalled_facts is not None else facts
    return {
        "enabled": bool(profile.enabled) if profile else False,
        "profile_found": bool(profile),
        "summary_used": bool(profile and profile.enabled and (profile.summary or "").strip()),
        "facts_count": len(facts),
        "facts_total": len(facts),
        "facts_recalled": len(recalled),
        "preferences_keys": sorted(normalize_preferences(profile.preferences).keys()) if profile and profile.enabled else [],
        "session_summary_used": bool(session_summary_used),
    }


def normalize_summary(value) -> str:
    """规整并严格截断长效记忆摘要。"""
    return str(value or "").strip()[:MAX_MEMORY_SUMMARY_CHARS]


def normalize_facts(value) -> list[dict]:
    """
    🧹 规整并衰减排序长效记忆事实列表。
    支持输入字符串列表或字典列表，处理字段补全、去重合并与时间衰减排序，最后截断保留前 50 条。
    """
    if not isinstance(value, list):
        return []

    import re
    import uuid

    current_time = datetime.now(timezone.utc)
    validated_items = []

    # 1. 字段格式化与有效性校验 🛡️
    for item in value:
        if isinstance(item, str):
            clean_text = item.strip()
            if not clean_text:
                continue
            validated_items.append({
                "id": uuid.uuid4().hex[:8],
                "text": clean_text,
                "source": "user",
                "created_at": current_time.isoformat(),
                "score": 1.0,
            })
        elif isinstance(item, dict):
            # 避免直接修改入参对象
            item_copy = dict(item)
            
            # 校验并清洗 text 字段
            text_val = item_copy.get("text")
            if text_val is None:
                continue
            clean_text = str(text_val).strip()
            if not clean_text:
                continue
            item_copy["text"] = clean_text

            # 校验并补全 id 字段
            id_val = item_copy.get("id")
            if not isinstance(id_val, str) or not id_val.strip():
                item_copy["id"] = uuid.uuid4().hex[:8]
            else:
                item_copy["id"] = id_val.strip()

            # 校验并补全 source 字段
            source_val = item_copy.get("source")
            if not isinstance(source_val, str) or not source_val.strip():
                item_copy["source"] = "user"
            else:
                item_copy["source"] = source_val.strip()

            # 校验并补全 created_at 字段
            created_at_val = item_copy.get("created_at")
            try:
                if isinstance(created_at_val, str):
                    dt = datetime.fromisoformat(created_at_val)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    item_copy["created_at"] = dt.isoformat()
                else:
                    raise ValueError()
            except Exception:
                item_copy["created_at"] = current_time.isoformat()

            # 校验并补全 score 字段
            score_val = item_copy.get("score")
            try:
                score_float = float(score_val)
                # 约束 score 范围在 0.0 到 1.0 之间
                if score_float < 0.0 or score_float > 1.0:
                    score_float = 1.0
                item_copy["score"] = score_float
            except (ValueError, TypeError):
                item_copy["score"] = 1.0

            validated_items.append(item_copy)

    # 2. 文本去重与合并 (Deduplication) 🔄
    # 规则：统一转换为小写，去除非字母、数字 and 中文字符
    seen = {}  # normalized_key -> item
    unique_items = []

    def _normalize_text_key(text: str) -> str:
        text_lower = text.lower()
        # 移除非英文、非数字、非中文的字符
        return re.sub(r"[^a-z0-9\u3400-\u4dbf\u4e00-\u9fff]", "", text_lower)

    for item in validated_items:
        key = _normalize_text_key(item["text"])
        if key in seen:
            existing = seen[key]
            # 更新分数（保留最高分）
            existing["score"] = max(existing["score"], item["score"])
            # 更新创建时间（保留较新时间）
            try:
                dt_existing = datetime.fromisoformat(existing["created_at"])
                dt_new = datetime.fromisoformat(item["created_at"])
                if dt_existing.tzinfo is None:
                    dt_existing = dt_existing.replace(tzinfo=timezone.utc)
                if dt_new.tzinfo is None:
                    dt_new = dt_new.replace(tzinfo=timezone.utc)
                if dt_new > dt_existing:
                    existing["created_at"] = item["created_at"]
            except Exception:
                pass
        else:
            seen[key] = item
            unique_items.append(item)

    # 3. 衰减打分与排序 (Decay & Sorting) 📉
    def _calc_decayed_score(item: dict) -> float:
        try:
            created_at_time = datetime.fromisoformat(item["created_at"])
            if created_at_time.tzinfo is None:
                created_at_time = created_at_time.replace(tzinfo=timezone.utc)
        except Exception:
            created_at_time = current_time
        
        days_old = (current_time - created_at_time).total_seconds() / 86400.0
        days_old = max(0.0, days_old)
        return item["score"] * (0.95 ** days_old)

    unique_items.sort(key=_calc_decayed_score, reverse=True)

    # 4. 截断最多 50 条 ✂️
    return unique_items[:MAX_MEMORY_FACTS]


def normalize_preferences(value) -> dict:
    """
    🛡️ 强数据清洗校验：
        规整用户偏好键值对（KV），剔除空白 Key 和无效 Value，只保留合法的字符串、整型、浮点型和布尔值等基本类型。
        防止嵌套的 JSON 或非法二进制注入数据库。
    """
    if not isinstance(value, dict):
        return {}
    normalized = {}
    for key, raw_value in value.items():
        clean_key = str(key or "").strip()
        if not clean_key:
            continue
        clean_value = normalize_preference_value(raw_value)
        if clean_value is not None:
            normalized[clean_key] = clean_value
    return normalized


def normalize_preference_value(value):
    """
    防御性的偏好值类型限定过滤器。
    仅接纳 Python 基础标量数据类型及基础列表。
    """
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        normalized = []
        for item in value:
            if isinstance(item, (str, int, float, bool)):
                normalized.append(item)
        return normalized
    return None


def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度；任一向量为零向量时返回 0。"""
    import math

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def recall_facts(profile: AgentMemoryProfile | None, query: str, k: int) -> list[dict]:
    """
    按 query 在进程内对 profile.facts 做 embedding 余弦相似度召回 top-k（分数 >= 阈值才算召回）。

    🎯 为何改为进程内召回（不再依赖 Milvus）：
        - 事实源就是 DB 里的 profile.facts（normalize 后上限 50 条），逐条 embedding 成本可控，
          且命中 embedding 缓存后几乎零开销；租户隔离天然成立（facts 取自该 profile）。
        - 彻底 hermetic：不受向量库可用性 / 最终一致性 / 跨环境残留影响——修掉了原先直连 Milvus
          导致召回随其状态漂移（且无相似度阈值、命中即全量返回）的非确定性问题。

    🛡️ embedding 调用失败（如未配置嵌入 Key）时降级返回全量 facts 的 top-k，不阻断主流程。
    """
    if not profile or not profile.enabled:
        return []
    all_facts = normalize_facts(profile.facts)
    if not all_facts:
        return []
    q = (query or "").strip()
    if not q:
        return all_facts[:k]

    import logging

    logger = logging.getLogger(__name__)
    try:
        from core.config import get_settings
        from core.integrations.llm import OpenAICompatibleProvider

        threshold = get_settings().memory_recall_min_score
        provider = OpenAICompatibleProvider()
        query_vec = provider.embed(q)
        scored = []
        for fact in all_facts:
            score = _cosine(query_vec, provider.embed(fact["text"]))
            if score >= threshold:
                scored.append((score, fact))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [fact for _, fact in scored[:k]]
    except Exception as e:
        logger.warning("In-process fact recall failed, returning all facts: %s", e)
        return all_facts[:k]


def recall_profile_memory(
    profile: AgentMemoryProfile | None,
    query: str,
    k: int = 5,
    *,
    facts: list[dict] | None = None,
) -> str:
    """
    召回长期画像记忆：
    - summary 整体带上
    - facts 按 query 检索 top-k
    - preferences 全量带上

    🎯 性能：若调用方已经召回过 facts（如 workflow 同一轮已调 recall_facts 用于观测事件），
        通过 `facts` 传入复用，避免在同一回合内重复 embedding + 向量检索。
    """
    if not profile or not profile.enabled:
        return ""
    parts = []
    summary = (profile.summary or "").strip()
    if summary:
        parts.append(f"Long-term memory summary:\n{summary}")
    facts = recall_facts(profile, query=query, k=k) if facts is None else facts
    if facts:
        parts.append("Long-term memory facts:\n" + "\n".join(f"- {item['text']}" for item in facts))
    preferences = normalize_preferences(profile.preferences)
    if preferences:
        lines = [f"- {key}: {value}" for key, value in sorted(preferences.items())]
        parts.append("Long-term memory preferences:\n" + "\n".join(lines))
    return "\n\n".join(parts)

