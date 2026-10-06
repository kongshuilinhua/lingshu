from __future__ import annotations

import copy

from sqlalchemy.orm import Session

from core.db.models import (
    Agent,
    AgentKnowledgeBase,
    AgentMcpBinding,
    AgentSkillBinding,
    AgentMemoryProfile,
    AgentSettings,
    AgentTool,
    AgentVersion,
    Feedback,
    KnowledgeBase,
    Message,
    McpServer,
    ModelConfig,
    Run,
    RunStep,
    Session as ChatSession,
    SessionMemory,
    Tool,
    UserModelConfig,
    WorkflowDefinition,
)
from core.config import get_settings
from core.services.bootstrap import DEFAULT_WORKFLOW
from core.services.models import model_payload
from core.services.tools import tool_payload
from core.services.user_models import user_model_snapshot
from core.services.mcp_registry import agent_bindings_payload
from core.services.skills import skill_bindings

# 🎯 系统预设推荐词
DEFAULT_SUGGESTED_QUESTIONS = [
    "这个智能体能帮我做什么？",
    "请基于知识库回答一个问题。",
]

# 🎯 平台预设记忆参数、RAG 融合检索指标及工具绑定默认规则
DEFAULT_MEMORY = {"enabled": False, "strategy": "session_summary", "max_messages": 12}
DEFAULT_RAG = {
    "enabled_by_default": True,
    "top_k": 4,
    "dense_top_k": 12,
    "bm25_top_k": 12,
    "rrf_k": 60,
    "rerank_enabled": True,
    "rerank_top_n": 6,
    "cache_enabled": True,
    "refuse_when_no_evidence": True,
}
DEFAULT_TOOL_POLICY = {"mode": "auto", "allowed_tool_names": [], "web_search_enabled": False}
DEFAULT_QUERY_UNDERSTANDING = {
    "enabled": True,
    "model": None,
    "confidence_threshold": 0.5,
    "clarify_enabled": True,
    "history_turns": 4,
}

# 🛡️ 兼容性演进：旧版本系统词汇转换为个人友好词汇（智能体去团队化规整）
LEGACY_TEAM_AGENT_TEXT = {
    "description": (
        "面向团队内部使用的自定义智能体。",
        "用于个人或项目场景的自定义智能体。",
    ),
    "opening_message": (
        "你好，我是你的团队智能体。",
        "你好，我是你的智能体。",
    ),
    "system_prompt": (
        "你是一个谨慎、清晰的团队智能体。优先使用绑定知识库和工具输出回答。",
        "你是一个谨慎、清晰的智能体。优先使用绑定知识库和工具输出回答。",
    ),
}


def agent_summary(agent: Agent) -> dict:
    """
    智能体模型元数据精简提取（DTO 映射）。
    
    🛡️ 防御性设计：
        - 标题及描述等输出时，通过 `current_agent_text` 动态清洗历史旧文案，保证统一的用户体验。
    """
    return {
        "id": agent.id,
        "name": agent.name,
        "avatar": agent.avatar,
        "model_id": agent.model_id,
        "user_model_config_id": agent.user_model_config_id,
        "description": current_agent_text("description", agent.description),
        "opening_message": current_agent_text("opening_message", agent.opening_message),
        "model": agent.model,
        "temperature": agent.temperature,
        "status": agent.status,
        "is_template": agent.is_template,
        "created_by": agent.created_by,
        "published_version_id": agent.published_version_id,
        "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
    }


def get_agent_detail(db: Session, agent: Agent) -> dict:
    """
    获取智能体最完整的资产元配置（Agent Detail Snapshot）。

    🎯 意图与工程大局观：
        构建一个 Agent 运行时所需的全部配置面，包含绑定关系数据库关系（知识库、工具）、工作流图元定义、
        Agent 高级设置、以及私有 BYOK 模型快照等。该输出常被用作发布历史版本快照。
    """
    kb_ids = [
        row.knowledge_base_id
        for row in db.query(AgentKnowledgeBase).filter(AgentKnowledgeBase.agent_id == agent.id).all()
    ]
    # ⚡ 性能考虑：外键.in_ 查询，避免 O(N) 的 N+1 次数据库往返
    tool_ids = [row.tool_id for row in db.query(AgentTool).filter(AgentTool.agent_id == agent.id).all()]
    tools = db.query(Tool).filter(Tool.id.in_(tool_ids)).all() if tool_ids else []
    workflow = db.query(WorkflowDefinition).filter(WorkflowDefinition.agent_id == agent.id).first()
    settings = ensure_agent_settings(db, agent.id)
    model_config = db.get(ModelConfig, agent.model_id) if agent.model_id else None
    user_model_config = db.get(UserModelConfig, agent.user_model_config_id) if agent.user_model_config_id else None
    return {
        **agent_summary(agent),
        "user_model_config_id": agent.user_model_config_id,
        "system_prompt": current_agent_text("system_prompt", agent.system_prompt),
        "knowledge_base_ids": kb_ids,
        "tools": [tool_payload(tool) for tool in tools],
        "mcp_bindings": agent_bindings_payload(db, agent.id),
        "skill_bindings": skill_bindings(db, agent.id),
        "workflow": workflow.nodes if workflow else DEFAULT_WORKFLOW,
        "model_config": model_payload(model_config) if model_config else None,
        "user_model_config": user_model_snapshot(user_model_config),
        "suggested_questions": settings.suggested_questions or [],
        "variables": settings.variables or [],
        "memory": normalize_memory(settings.memory),
        "rag": normalize_rag(settings.rag),
        "tool_policy": normalize_tool_policy(settings.tool_policy),
        "query_understanding": normalize_query_understanding(settings.query_understanding),
    }


def create_agent(db: Session, *, workspace_id: int, user_id: int, payload: dict) -> Agent:
    """
    创建智能体及其所有依赖关系记录的统一事务函数。
    """
    agent = Agent(
        workspace_id=workspace_id,
        model_id=payload.get("model_id"),
        user_model_config_id=payload.get("user_model_config_id"),
        name=payload["name"],
        avatar=payload.get("avatar") or "AI",
        description=payload.get("description") or "",
        opening_message=payload.get("opening_message") or "",
        system_prompt=payload.get("system_prompt") or "",
        model=payload.get("model") or "qwen-plus",
        temperature=payload.get("temperature", 0.4),
        created_by=user_id,
    )
    db.add(agent)
    db.flush() # flush 以提前获取物理自增 id
    db.add(WorkflowDefinition(agent_id=agent.id, nodes=DEFAULT_WORKFLOW))
    db.add(
        AgentSettings(
            agent_id=agent.id,
            suggested_questions=normalize_questions(payload.get("suggested_questions")),
            variables=normalize_variables(payload.get("variables")),
            memory=normalize_memory(payload.get("memory")),
            rag=normalize_rag(payload.get("rag")),
            tool_policy=normalize_tool_policy(payload.get("tool_policy")),
            query_understanding=normalize_query_understanding(payload.get("query_understanding")),
        )
    )
    _replace_agent_knowledge(db, agent.id, payload.get("knowledge_base_ids") or [])
    _replace_agent_tools(db, agent.id, payload.get("tool_ids") or [])
    db.commit()
    db.refresh(agent)
    return agent


def update_agent(db: Session, agent: Agent, payload: dict) -> Agent:
    """
    更新智能体核心属性及其映射资产的统一事务函数。
    """
    for key in ["model_id", "user_model_config_id"]:
        if key in payload:
            setattr(agent, key, payload[key])
    for key in ["name", "avatar", "description", "opening_message", "system_prompt", "model", "temperature"]:
        if key in payload and payload[key] is not None:
            setattr(agent, key, payload[key])
    if "knowledge_base_ids" in payload:
        _replace_agent_knowledge(db, agent.id, payload["knowledge_base_ids"] or [])
    if "tool_ids" in payload:
        _replace_agent_tools(db, agent.id, payload["tool_ids"] or [])
    if any(key in payload for key in ["suggested_questions", "variables", "memory", "rag", "tool_policy", "query_understanding"]):
        settings = ensure_agent_settings(db, agent.id)
        if "suggested_questions" in payload:
            settings.suggested_questions = normalize_questions(payload["suggested_questions"])
        if "variables" in payload:
            settings.variables = normalize_variables(payload["variables"])
        if "memory" in payload:
            settings.memory = normalize_memory(payload["memory"])
        if "rag" in payload:
            settings.rag = normalize_rag(payload["rag"])
        if "tool_policy" in payload:
            settings.tool_policy = normalize_tool_policy(payload["tool_policy"])
        if "query_understanding" in payload:
            settings.query_understanding = normalize_query_understanding(payload["query_understanding"])
    db.commit()
    db.refresh(agent)
    return agent


def publish_agent(db: Session, agent: Agent, user_id: int, *, require_review: bool = False) -> AgentVersion:
    """
    智能体版本快照发布。

    🎯 意图与工程大局观：
        为当前草稿态 Agent 生成独立的持久化不可变 Snapshot（版本自增 1）。
        支持平台审批管理流：若开启审核机制，状态置为 `pending_review`，不更改当前线上版本；
        否则自动生效，将 `published_version_id` 修改为本最新生成的快照 ID。
    """
    latest = db.query(AgentVersion).filter(AgentVersion.agent_id == agent.id).order_by(AgentVersion.version.desc()).first()
    version_number = (latest.version + 1) if latest else 1
    snapshot = get_agent_detail(db, agent)
    version = AgentVersion(agent_id=agent.id, version=version_number, snapshot=snapshot, created_by=user_id)
    db.add(version)
    db.flush()
    if require_review:
        agent.status = "pending_review"
    else:
        agent.status = "published"
        agent.published_version_id = version.id
    db.commit()
    db.refresh(version)
    return version


def ensure_template_agents_published(db: Session, workspace_id: int) -> None:
    """
    系统自举后置操作：确保特定工作区内所有处于 template 状态的智能体都已经拥有发布版本快照，
    解决应用初始化或模板市场数据自举后的快照引用状态完备性。
    """
    templates = (
        db.query(Agent)
        .filter(Agent.workspace_id == workspace_id, Agent.is_template.is_(True))
        .all()
    )
    changed = False
    for agent in templates:
        if agent.status == "published" and agent.published_version_id:
            continue
        agent.status = "published"
        version = latest_agent_version(db, agent)
        if not version:
            version = AgentVersion(
                agent_id=agent.id,
                version=1,
                snapshot=get_agent_detail(db, agent),
                created_by=agent.created_by,
            )
            db.add(version)
            db.flush()
        agent.published_version_id = version.id
        changed = True
    if changed:
        db.commit()


def latest_agent_version(db: Session, agent: Agent) -> AgentVersion | None:
    """获取最新快照纪录。"""
    return db.query(AgentVersion).filter(AgentVersion.agent_id == agent.id).order_by(AgentVersion.version.desc()).first()


def approve_agent(db: Session, agent: Agent, reviewer_id: int) -> AgentVersion:
    """管理员审批同意 Agent 模版发布上线，强制激活最新草稿版本快照为线上当前版本。"""
    version = latest_agent_version(db, agent)
    if not version:
        version = publish_agent(db, agent, reviewer_id, require_review=False)
    agent.status = "published"
    agent.published_version_id = version.id
    db.commit()
    db.refresh(version)
    return version


def reject_agent(db: Session, agent: Agent) -> Agent:
    """管理员驳回模板智能体上线申请。"""
    agent.status = "rejected"
    db.commit()
    db.refresh(agent)
    return agent


def delete_agent(db: Session, agent: Agent) -> None:
    """
    智能体级联物理擦除（级联灾难清理）。

    🛡️ 坚固的防御性安全防线：
        - 系统级只读模板智能体禁止被任意物理删除（is_template 保护），防止公共资源损坏。
        
    ⚡ 性能与并发冲突思考：
        - 采取关系关联表的大批量主动删除策略。
        - 通过 `synchronize_session=False` 告诉 SQLAlchemy 绕过一级缓存对象查找，直接向数据库发起批式物理删除 SQL，
          规避了由于加载数万条历史 Message 导致的进程 OOM 问题，极大地压缩了事务耗时和 I/O 吞吐。
    """
    if agent.is_template:
        raise ValueError("Template agents cannot be deleted")

    session_ids = [
        row.id
        for row in db.query(ChatSession.id).filter(ChatSession.agent_id == agent.id).all()
    ]
    message_ids = []
    if session_ids:
        message_ids = [
            row.id
            for row in db.query(Message.id).filter(Message.session_id.in_(session_ids)).all()
        ]
    run_ids = [
        row.id
        for row in db.query(Run.id).filter(Run.agent_id == agent.id).all()
    ]

    # 1. 物理删除关联 Trace 反馈
    if message_ids:
        db.query(Feedback).filter(Feedback.message_id.in_(message_ids)).delete(synchronize_session=False)
    # 2. 物理删除关联工作流 Trace Step 轨迹
    if run_ids:
        db.query(RunStep).filter(RunStep.run_id.in_(run_ids)).delete(synchronize_session=False)
    # 3. 物理删除关联会话滚动记忆、历史会话消息
    if session_ids:
        db.query(SessionMemory).filter(SessionMemory.session_id.in_(session_ids)).delete(synchronize_session=False)
        db.query(Message).filter(Message.session_id.in_(session_ids)).delete(synchronize_session=False)
    # 4. 物理删除执行 Run、会话、绑定关系以及配置项
    db.query(Run).filter(Run.agent_id == agent.id).delete(synchronize_session=False)
    db.query(ChatSession).filter(ChatSession.agent_id == agent.id).delete(synchronize_session=False)
    db.query(AgentKnowledgeBase).filter(AgentKnowledgeBase.agent_id == agent.id).delete(synchronize_session=False)
    db.query(AgentMemoryProfile).filter(AgentMemoryProfile.agent_id == agent.id).delete(synchronize_session=False)
    db.query(AgentTool).filter(AgentTool.agent_id == agent.id).delete(synchronize_session=False)
    db.query(AgentSkillBinding).filter(AgentSkillBinding.agent_id == agent.id).delete(synchronize_session=False)
    db.query(AgentMcpBinding).filter(AgentMcpBinding.agent_id == agent.id).delete(synchronize_session=False)
    db.query(AgentSettings).filter(AgentSettings.agent_id == agent.id).delete(synchronize_session=False)
    db.query(WorkflowDefinition).filter(WorkflowDefinition.agent_id == agent.id).delete(synchronize_session=False)
    db.query(AgentVersion).filter(AgentVersion.agent_id == agent.id).delete(synchronize_session=False)
    db.delete(agent)
    db.commit()


def market_agent_summary(agent: Agent, version: AgentVersion | None = None) -> dict:
    """提取市场模版智能体详情（DTO）。"""
    snapshot = normalize_snapshot_text((version.snapshot if version else None) or {})
    return {
        "id": agent.id,
        "name": snapshot.get("name") or agent.name,
        "avatar": snapshot.get("avatar") or agent.avatar,
        "description": snapshot.get("description") or agent.description,
        "status": agent.status,
        "version": version.version if version else None,
        "published_version_id": agent.published_version_id,
        "created_by": agent.created_by,
        "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
    }


def copy_agent_from_market(db: Session, *, source: Agent, user_id: int, workspace_id: int) -> Agent:
    """
    从智能体广场拷贝（克隆）一个公开分享的智能体到当前租户的私有空间中。

    🎯 意图与工程大局观：
        - 必须依靠已发布的快照版本（`published_version_id`）为源，绝不能使用其可变的草稿。
        - 拷贝包含完整的工作流结构编排，并在最后强制追加“副本”后缀解决标题冲突。
    """
    version = db.get(AgentVersion, source.published_version_id) if source.published_version_id else None
    if not version:
        raise ValueError("Agent has no approved version")
    snapshot = normalize_snapshot_text(version.snapshot or {})
    copied = create_agent(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        payload={
            "name": f"{snapshot.get('name') or source.name} 副本",
            "avatar": snapshot.get("avatar") or source.avatar,
            "description": snapshot.get("description") or source.description,
            "opening_message": snapshot.get("opening_message") or source.opening_message,
            "system_prompt": snapshot.get("system_prompt") or source.system_prompt,
            "model": snapshot.get("model") or source.model,
            "model_id": snapshot.get("model_id") or source.model_id,
            "user_model_config_id": snapshot.get("user_model_config_id") or source.user_model_config_id,
            "temperature": snapshot.get("temperature", source.temperature),
            "knowledge_base_ids": snapshot.get("knowledge_base_ids") or [],
            "tool_ids": [tool.get("id") for tool in snapshot.get("tools", []) if tool.get("id")],
            "suggested_questions": snapshot.get("suggested_questions") or [],
            "variables": snapshot.get("variables") or [],
            "memory": snapshot.get("memory") or DEFAULT_MEMORY,
            "rag": snapshot.get("rag") or DEFAULT_RAG,
            "tool_policy": snapshot.get("tool_policy") or DEFAULT_TOOL_POLICY,
            "query_understanding": snapshot.get("query_understanding") or DEFAULT_QUERY_UNDERSTANDING,
        },
    )
    workflow = db.query(WorkflowDefinition).filter(WorkflowDefinition.agent_id == copied.id).first()
    if workflow:
        workflow.nodes = snapshot.get("workflow") or DEFAULT_WORKFLOW
    for item in snapshot.get("mcp_bindings") or []:
        server = db.get(McpServer, item.get("server_id"))
        if not server or not server.enabled or not server.is_listed or server.workspace_id not in {None, workspace_id}:
            continue
        selected = item.get("selected_tools") or []
        known = {tool.get("name") for tool in (server.catalog or {}).get("tools") or []}
        if selected and set(selected).issubset(known):
            db.add(AgentMcpBinding(
                agent_id=copied.id, mcp_server_id=server.id,
                enabled=bool(item.get("enabled", True)), config={"selected_tools": selected},
            ))
    from core.db.models import Skill, SkillVersion
    for item in snapshot.get('skill_bindings') or []:
        skill = db.get(Skill, item.get('skill_id'))
        skill_version = db.get(SkillVersion, item.get('version_id'))
        if skill and skill.enabled and skill.is_listed and skill.workspace_id == workspace_id and skill_version and skill_version.skill_id == skill.id:
            db.add(AgentSkillBinding(agent_id=copied.id, skill_id=skill.id, version_id=skill_version.id, enabled=item.get('enabled', True)))
    db.commit()
    db.refresh(copied)
    return copied


def current_agent_text(field: str, value: str) -> str:
    """清洗老版本遗留下的“团队智能体”硬编码说明文本，抹平历史遗留语境。"""
    old_value, new_value = LEGACY_TEAM_AGENT_TEXT.get(field, ("", ""))
    return new_value if value == old_value else value


def normalize_snapshot_text(snapshot: dict) -> dict:
    """深拷贝并在快照文本层面清洗遗留旧词汇。"""
    data = copy.deepcopy(snapshot)
    for field in ["description", "opening_message", "system_prompt"]:
        if field in data:
            data[field] = current_agent_text(field, data[field])
    return data


def ensure_agent_settings(db: Session, agent_id: int) -> AgentSettings:
    """智能体高级设置防线：如缺失（旧智能体或新数据自举时），自动通过本工厂方法生成默认的高级设置实体，保证数据一致性。"""
    settings = db.query(AgentSettings).filter(AgentSettings.agent_id == agent_id).first()
    if settings:
        return settings
    settings = AgentSettings(
        agent_id=agent_id,
        suggested_questions=DEFAULT_SUGGESTED_QUESTIONS,
        variables=[],
        memory=DEFAULT_MEMORY,
        rag=DEFAULT_RAG,
        tool_policy=DEFAULT_TOOL_POLICY,
        query_understanding=DEFAULT_QUERY_UNDERSTANDING,
    )
    db.add(settings)
    db.flush()
    return settings


def normalize_questions(value) -> list[str]:
    """清洗并强限制预设提问条数（硬上限 8 条，防前端报文滥用）。"""
    if not value:
        return []
    return [str(item).strip() for item in value if str(item).strip()][:8]


def normalize_variables(value) -> list[dict]:
    """
    清洗并限制智能体全局动态自定义插值变量。
    
    🧠 魔鬼数字限制：
        - 强制限制变量上限为 20 个，避免大体积冗余变量撑爆系统上下文 Token 大小。
        - 变量类型强白名单判定：只允许 `string`、`number`、`boolean`，防代码注入或恶意数据污染。
    """
    if not value:
        return []
    allowed_types = {"string", "number", "boolean"}
    normalized = []
    for item in value:
        data = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        var_type = data.get("type") if data.get("type") in allowed_types else "string"
        key = str(data.get("key", "")).strip()
        label = str(data.get("label", "")).strip() or key
        if not key:
            continue
        normalized.append(
            {
                "key": key,
                "label": label,
                "type": var_type,
                "required": bool(data.get("required", False)),
                "default_value": data.get("default_value"),
            }
        )
    return normalized[:20]


def normalize_memory(value) -> dict:
    """
    规整短期记忆参数。
    最大对话轮次（max_messages）上限硬限制为 100 轮，规避因对话记忆滚存过长触发的 LLM 溢出挂起异常。
    """
    data = value.model_dump() if hasattr(value, "model_dump") else dict(value or {})
    strategy = data.get("strategy") if data.get("strategy") == "session_summary" else "session_summary"
    max_messages = int(data.get("max_messages") or 12)
    return {"enabled": bool(data.get("enabled", False)), "strategy": strategy, "max_messages": max(1, min(max_messages, 100))}


def normalize_rag(value) -> dict:
    """
    规整 RAG 混合召回和算分重排参数。
    
    🧠 核心阈值与边界限制（抗网络超载限流防御）：
        - 全局 RAG 最终截断 top_k 限制在 1 - 20 内。
        - 多路检索 Dense/BM25 通道单路最多只提取 50 片，防 Milvus / 数据库因大批量读取导致内存暴涨。
        - Rerank 最多重排 20 片，在重排模型的并发能力与耗时成本之间取得最佳的折中，防止 Rerank 超时卡死流程。
    """
    settings = get_settings()
    data = value.model_dump() if hasattr(value, "model_dump") else dict(value or {})
    top_k = int(data.get("top_k") or settings.rag_top_k)
    dense_top_k = int(data.get("dense_top_k") or settings.rag_dense_top_k)
    bm25_top_k = int(data.get("bm25_top_k") or settings.rag_bm25_top_k)
    rrf_k = int(data.get("rrf_k") or settings.rag_rrf_k)
    rerank_top_n = int(data.get("rerank_top_n") or settings.rag_rerank_top_n)
    return {
        "enabled_by_default": bool(data.get("enabled_by_default", True)),
        "top_k": max(1, min(top_k, 20)),
        "dense_top_k": max(1, min(dense_top_k, 50)),
        "bm25_top_k": max(1, min(bm25_top_k, 50)),
        "rrf_k": max(1, min(rrf_k, 200)),
        "rerank_enabled": bool(data.get("rerank_enabled", settings.rag_rerank_enabled)),
        "rerank_top_n": max(1, min(rerank_top_n, 20)),
        "cache_enabled": bool(data.get("cache_enabled", settings.rag_cache_enabled)),
        "refuse_when_no_evidence": bool(data.get("refuse_when_no_evidence", settings.rag_refuse_when_no_evidence)),
    }


def normalize_tool_policy(value) -> dict:
    """规整工具调度策略允许列表。"""
    data = value.model_dump() if hasattr(value, "model_dump") else dict(value or {})
    mode = data.get("mode") if data.get("mode") == "auto" else "auto"
    names = [str(item).strip() for item in data.get("allowed_tool_names", []) if str(item).strip()]
    return {"mode": mode, "allowed_tool_names": names[:50], "web_search_enabled": data.get('web_search_enabled') is True}


def normalize_query_understanding(value) -> dict:
    """规整查询理解层配置。

    🧠 边界限制：
        - confidence_threshold 夹紧到 [0.0, 1.0]。
        - history_turns 夹紧到 [1, 12]，避免改写时塞入过多历史撑爆 Token。
        - model 为空白字符串时归一为 None（表示复用 agent 当前模型）。
    """
    data = value.model_dump() if hasattr(value, "model_dump") else dict(value or {})
    threshold = float(data.get("confidence_threshold", 0.5) or 0.5)
    history_turns = int(data.get("history_turns") or 4)
    raw_model = data.get("model")
    model = str(raw_model).strip() if raw_model else ""
    return {
        "enabled": bool(data.get("enabled", True)),
        "model": model or None,
        "confidence_threshold": min(max(threshold, 0.0), 1.0),
        "clarify_enabled": bool(data.get("clarify_enabled", True)),
        "history_turns": max(1, min(history_turns, 12)),
    }


def _replace_agent_knowledge(db: Session, agent_id: int, knowledge_base_ids: list[int]) -> None:
    """全量覆盖智能体绑定的知识库。"""
    db.query(AgentKnowledgeBase).filter(AgentKnowledgeBase.agent_id == agent_id).delete()
    for kb_id in knowledge_base_ids:
        db.add(AgentKnowledgeBase(agent_id=agent_id, knowledge_base_id=kb_id))


def _replace_agent_tools(db: Session, agent_id: int, tool_ids: list[int]) -> None:
    """全量覆盖智能体绑定的外部工具。"""
    db.query(AgentTool).filter(AgentTool.agent_id == agent_id).delete()
    for tool_id in tool_ids:
        db.add(AgentTool(agent_id=agent_id, tool_id=tool_id))


def workspace_kb_exists(db: Session, workspace_id: int, kb_id: int) -> bool:
    """校验某个知识库是否存在于工作空间中。"""
    return db.query(KnowledgeBase).filter(KnowledgeBase.workspace_id == workspace_id, KnowledgeBase.id == kb_id).first() is not None
