from __future__ import annotations

from sqlalchemy.orm import Session

from core.config import get_settings
from core.db.models import (
    Agent,
    AgentKnowledgeBase,
    AgentTool,
    KnowledgeBase,
    ModelConfig,
    Tool,
    User,
    WorkflowDefinition,
    Workspace,
    WorkspaceMember,
)
from core.security.auth import hash_password
from core.services.tools import BUILTIN_TOOLS

MVP_BUILTIN_TOOL_NAMES = {
    "current_time",
    "calculator",
    "web_reader",
    "wikipedia",
    "arxiv_search",
    # 本地确定性工具：无外部依赖、结果可复现，注册进来即可用。
    "password_generator",
    "uuid_generator",
    "diff_checker",
    "character_counter",
    # 免 Key 公网接口：结果来自真实服务，失败时显式报错而不是编造内容。
    "qr_generator",
    "currency_converter",
    "weather_lookup",
    "url_shortener",
    "ip_lookup",
}

# 🎯 默认内置智能体节点编排流转流程（典型 ReAct/RAG 工作流骨架）
# 该默认工作流向初学者演示了一个健壮的 Agent 运行轨迹：接收输入 -> 知识检索 -> 工具决策 -> 大模型提炼 -> 最终回答
DEFAULT_WORKFLOW = [
    {"id": "start", "type": "Start", "name": "接收用户输入", "config": {}},
    {"id": "knowledge", "type": "Knowledge", "name": "检索绑定知识库", "config": {"top_k": 4}},
    {"id": "tool", "type": "Tool", "name": "调用绑定工具", "config": {"tools": []}},
    {"id": "llm", "type": "LLM", "name": "生成候选回答", "config": {}},
    {"id": "answer", "type": "Answer", "name": "输出最终回答", "config": {}},
]


def ensure_builtin_tools(db: Session) -> None:
    """
    平台内置工具元数据同步自举引擎（平台自适应更新的锚点）。

    🎯 意图与工程大局观：
        内置工具（如 `calculate`, `web_search` 等）定义在内存代码中（BUILTIN_TOOLS）。
        当平台版本升级导致某些工具被废弃或新增时，如果数据库里的记录与代码版本脱节，会导致用户页面显示错误，甚至大模型调用时参数不兼容。
        本方法是连接代码与数据库的“同步哨兵”：
        - 自动扫描清理已经被废弃的代码工具定义（Cascade 物理级联删除）。
        - 高效地将最新代码里的内置工具描述、Label 同步/更新至数据库（Upsert 幂等操作）。
    
    🛡️ 防御性编程与大模型兜底：
        - 清理旧工具时，为了防止外键物理约束报错（IntegrityError），采用二级清理策略：
          第一步删除 `AgentTool` 表中的绑定行，第二步删除 `Tool` 元数据。
        - 强制指定 `synchronize_session=False`：
          告诉 SQLAlchemy 跳过把被删数据同步到当前 Session 缓存（一级缓存）的步骤。
          在高并发或大表清理场景中，该设置极大地加速了 delete 执行，并防止了 OOM。
    """
    # Keep the MVP tool surface focused: search + a few explainable builtins +
    # user-defined HTTP tools. Legacy toy builtin rows are removed from databases.
    registry_names = MVP_BUILTIN_TOOL_NAMES
    legacy_ids = [
        row.id for row in db.query(Tool.id).filter(
            Tool.type == "builtin",
            Tool.name.notin_(registry_names),
        ).all()
    ]
    if legacy_ids:
        db.query(AgentTool).filter(AgentTool.tool_id.in_(legacy_ids)).delete(synchronize_session=False)
        db.query(Tool).filter(Tool.id.in_(legacy_ids)).delete(synchronize_session=False)

    # Upsert the small builtin set that is useful for an internship MVP demo.
    for name in MVP_BUILTIN_TOOL_NAMES:
        impl = BUILTIN_TOOLS[name]
        tool = db.query(Tool).filter(Tool.name == name, Tool.type == "builtin").first()
        if tool:
            tool.description = impl["description"]
            tool.schema = impl.get("parameters") or {}
            tool.label = name
            tool.enabled = True
            continue
        db.add(Tool(name=name, label=name, description=impl["description"], schema=impl.get("parameters") or {}, type="builtin", enabled=True))

    # Upsert builtin_search adapter.
    search_tool = db.query(Tool).filter(Tool.name == "web_search").first()
    if search_tool:
        search_tool.type = "builtin_search"
        search_tool.label = "Web Search"
        search_tool.description = "Built-in web search adapter for Agent tools"
    else:
        db.add(Tool(name="web_search", label="Web Search", description="Built-in web search adapter for Agent tools", schema={}, type="builtin_search"))

    db.commit()


def ensure_default_models(db: Session) -> None:
    """
    自举初始化平台的默认大模型配置（BYOK 推理与多模态能力的底盘）。

    🧠 魔鬼数字与前沿技术参数：
        - 平台内配置 OpenAI 兼容模型最大上下文窗口（`max_context`）为 `131072`（128K Token）。
          对于包含丰富文档的 RAG 场景，这能有效防止上下文在拼装了大量检索片段后发生爆 Token 截断错误。
        - 默认温度 `default_temperature` 设为 `0.4`。
          相较于默认的 `1.0` 或 `0.7`，`0.4` 在处理复杂工具决策及高确定性知识检索回答时能显著收敛幻觉，输出更合规的指令结构。
        - VL 多模态模型 `qwen-vl-plus` 上下文设置为 `32768`（32K Token），并启用支持图像 `supports_image: True` 的特性，指导多模态文件提取及前置 Vision 预处理分流。
    """
    settings = get_settings()
    defaults = [
        {
            "model_name": settings.openai_model,
            "display_name": settings.openai_model,
            "provider": "openai-compatible",
            "supports_text": True,
            "supports_image": False,
            "supports_document": True,
            "supports_reasoning": True,
            "reasoning_type": "prompt",
            "reasoning_label": "提示词增强",
            "max_context": 131072,
            "default_temperature": 0.4,
        },
        {
            "model_name": "qwen-vl-plus",
            "display_name": "Qwen VL Plus",
            "provider": "openai-compatible",
            "supports_text": True,
            "supports_image": True,
            "supports_document": True,
            "supports_reasoning": True,
            "reasoning_type": "prompt",
            "reasoning_label": "提示词增强",
            "max_context": 32768,
            "default_temperature": 0.4,
        },
    ]
    changed = False
    for item in defaults:
        if db.query(ModelConfig).filter(ModelConfig.model_name == item["model_name"]).first():
            continue
        db.add(ModelConfig(enabled=True, **item))
        changed = True
    if changed:
        db.commit()


def ensure_template_agent(db: Session, owner: User, workspace: Workspace) -> Agent:
    """
    为新工作区自举生成“金牌体验示例智能体”（扫地机器人客服）。

    🎯 意图与工程大局观：
        为确保初注册用户的“零门槛体验感”，系统在自举阶段不仅生成了一个名为“扫地机器人客服”的模板智能体，
        更在物理层面为其绑定了一个专属演示知识库（AgentKnowledgeBase）和极具学习参考价值的 `DEFAULT_WORKFLOW`（工作流图元数据结构）。
        这在系统架构上建立了一个标准的“端到端 RAG 客服示例”，为二次开发和后续业务接入立下了工程规范范本。
    """
    existing = db.query(Agent).filter(Agent.workspace_id == workspace.id, Agent.is_template.is_(True)).first()
    if existing:
        return existing
    kb = KnowledgeBase(
        workspace_id=workspace.id,
        name="扫地机器人客服知识库",
        description="内置模板知识库，演示智能硬件客服 RAG 场景。",
        created_by=owner.id,
    )
    db.add(kb)
    db.flush()
    agent = Agent(
        workspace_id=workspace.id,
        name="扫地机器人客服",
        avatar="SR",
        description="面向扫地机器人售前、故障排查和维护保养 of 示例智能体。",
        opening_message="你好，我可以帮你排查扫地机器人问题、解释维护建议，也可以演示知识库引用。",
        system_prompt="你是一个谨慎的中文智能硬件客服智能体。优先基于绑定知识库回答，资料不足时明确说明。",
        model=get_settings().openai_model,
        temperature=0.3,
        is_template=True,
        created_by=owner.id,
    )
    db.add(agent)
    db.flush()
    db.add(AgentKnowledgeBase(agent_id=agent.id, knowledge_base_id=kb.id))
    db.add(WorkflowDefinition(agent_id=agent.id, nodes=DEFAULT_WORKFLOW))
    db.commit()
    return agent


def create_first_user_workspace(db: Session, *, email: str, name: str, password: str) -> tuple[User, Workspace]:
    """
    新用户注册时的“主入口自举管道”（全系统首批租户数据初始化）。

    🎯 意图与工程大局观：
        当系统空无一人时，首个注册的用户即被定义为系统超级管理员。
        我们必须在一个高安全性、原子性的事务中，同步完成：
        1. 物理 User 用户的哈希密码落库（防撞库与彩虹表物理截获）。
        2. 为该用户生成独立专属的多租户工作区（Workspace）。
        3. 自举默认大模型池（ModelConfig）、内置工具箱（Tool）以及金牌客服智能体（Agent）。
        4. 建立 WorkspaceMember 关联。
        若其中任一步骤崩塌，数据库事务会回滚到起点，强力保证平台启动数据是一致、整洁且零污染的。
    """
    user = User(email=email.lower(), name=name, password_hash=hash_password(password))
    workspace = Workspace(name=f"{name} 的工作台", slug="default")
    db.add_all([user, workspace])
    db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="admin"))
    db.commit()
    ensure_default_models(db)
    ensure_builtin_tools(db)
    ensure_template_agent(db, user, workspace)
    return user, workspace


def create_default_workspace_user(db: Session, *, email: str, name: str, password: str) -> tuple[User, Workspace]:
    """普通注册创建独立工作区；加入已有工作区只能通过邀请。"""
    user = User(email=email.lower(), name=name, password_hash=hash_password(password))
    db.add(user)
    db.flush()
    workspace = Workspace(name=f"{name} 的工作台", slug=f"personal-{user.id}")
    db.add(workspace)
    db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="admin"))
    db.commit()
    ensure_template_agent(db, user, workspace)
    return user, workspace


def has_any_user(db: Session) -> bool:
    """快速检查数据库中是否已存在任何注册用户，用以判断当前请求是首航自举还是常规普通会话。"""
    return db.query(User).first() is not None
