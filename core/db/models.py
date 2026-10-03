from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import Base

# 🛡️ MySQL 的 TEXT 上限仅 64KB，学术 PDF/长会话正文轻松超出（迁移自 PG 无限 TEXT 时埋的坑）。
# 用 LONGTEXT 变体承载可能很大的正文列；其它方言仍退化为标准 Text。
LongText = Text().with_variant(LONGTEXT(), "mysql")


def now() -> datetime:
    """
    🎯 意图与工程大局观：
        统一获取 UTC 当前时间的辅助函数。
        用作 SQLAlchemy Column default 可调用对象参数，避免在服务启动时固定静态时间。

    ⚡ Python 3.12+ 兼容性：
        使用 datetime.now(timezone.utc) 替代已弃用的 datetime.utcnow()。
    """
    return datetime.now(timezone.utc)


class User(Base):
    """
    用户实体模型。
    
    🎯 意图与工程大局观：
        存储平台用户的基本账号信息与安全凭证。在 Agent 的流转中，作为多租户隔离的第一道防线。
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # ⚡ 边界与性能思考：email 作为登录唯一凭证，设置 255 长度限制并加索引用以加速登录和防重校验。
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    # 🛡️ 防御性设计：avatar_url 默认空字符串，避免前端渲染空值导致界面崩坏。
    avatar_url: Mapped[str] = mapped_column(Text, default="")
    # 🛡️ 安全设计：存储单向加盐加密后的密码哈希，绝对禁止明文存储。
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Workspace(Base):
    """
    工作空间（多租户隔离单元）模型。

    🎯 意图与工程大局观：
        Lingshu Agent 采用工作空间（Workspace）实现逻辑多租户隔离。
        每个工作空间拥有独立的智能体、知识库、工具以及计费资源。
    """
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    # ⚡ 边界与性能思考：slug 为 URL 友好型唯一标识符，加唯一索引方便路径寻址。
    slug: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class WorkspaceMember(Base):
    """
    工作空间成员映射模型（多对多关联加属性）。

    🎯 意图与工程大局观：
        维护用户与工作空间的从属和角色权限关系，用于 API 层 RBAC 权限判断（deps.py）。
    """
    __tablename__ = "workspace_members"
    # 🛡️ 唯一性约束：一个用户在一个工作空间内仅允许拥有一条成员角色纪录。
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # ⚡ 边界与性能思考：外键约束级联删除 `ondelete="CASCADE"` 并在本地建索引，防止删除工作空间时大量级联外键遍历导致数据库卡顿。
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # 🧠 魔鬼数字与前沿技术参数：role 通常设计为 "owner"、"admin"、"member"。
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    user: Mapped[User] = relationship()
    workspace: Mapped[Workspace] = relationship()


class WorkspaceInvite(Base):
    """
    工作空间成员邀请模型。
    """
    __tablename__ = "workspace_invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    token: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class UserModelConfig(Base):
    """
    用户私有模型（Custom BYOK）配置模型。

    🎯 意图与工程大局观：
        支持用户自带 Key（Bring Your Own Key）以接入自定义 LLM 提供方。
        存储 Base URL、解密密钥引用以及模型推理特性元数据。
    """
    __tablename__ = "user_model_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    provider: Mapped[str] = mapped_column(String(80), default="openai-compatible")
    base_url: Mapped[str] = mapped_column(String(500))
    # 🛡️ 安全设计：明文 API Key 必须经过 `encrypt_api_key()` 对称加密存储在此字段，严禁明文入库。
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    chat_model: Mapped[str] = mapped_column(String(160))
    
    # 🧠 魔鬼数字与前沿技术参数：
    # 标注模型是否具备视觉（Image Input）、多模态文档（Document Extraction）和深度推理能力，用于运行时能力过滤。
    supports_image: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_document: Mapped[bool] = mapped_column(Boolean, default=True)
    supports_reasoning: Mapped[bool] = mapped_column(Boolean, default=False)
    reasoning_type: Mapped[str] = mapped_column(String(20), default="none")  # 'native' / 'prompt' / 'none'
    reasoning_label: Mapped[str] = mapped_column(String(80), default="不支持")
    max_context: Mapped[int] = mapped_column(Integer, default=131072)  # 默认 128k 上下文窗口限制
    default_temperature: Mapped[float] = mapped_column(Float, default=0.4)  # 默认 0.4 提供较高稳定性
    
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


# 🛡️ PostgreSQL 部分局部唯一索引：确保一个用户全局只能激活一个“默认”私有模型配置，防止脏数据干扰。
Index(
    "ix_user_model_configs_one_default_per_user",
    UserModelConfig.user_id,
    unique=True,
    postgresql_where=text("is_default = true"),
).ddl_if(dialect="postgresql")


class Agent(Base):
    """
    智能体主体模型。

    🎯 意图与工程大局观：
        存储智能体的核心静态属性配置（系统提示词、温度、绑定模型等）。
        采用“草稿（Draft）”模式，实际生效运行时可能指向 `published_version_id`。
    """
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    model_id: Mapped[int | None] = mapped_column(ForeignKey("model_configs.id", ondelete="SET NULL"), nullable=True)
    user_model_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_model_configs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(160))
    avatar: Mapped[str] = mapped_column(Text, default="SA")
    description: Mapped[str] = mapped_column(Text, default="")
    opening_message: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(120), default="qwen-plus")
    temperature: Mapped[float] = mapped_column(Float, default=0.4)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # 'draft' / 'published'
    published_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_template: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class AgentVersion(Base):
    """
    智能体历史版本快照模型。

    🎯 意图与工程大局观：
        为 Agent 发布机制提供版本回溯和不变性快照支持。
        `snapshot` 存储了发布那一刻 Agent 的完整静态配置（Prompt, Tools, Workflow），
        确保哪怕之后 Agent 草稿被修改，历史对话依然调用发布版本的运行逻辑。
    """
    __tablename__ = "agent_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    # 🛡️ 容错设计：JSON 数据结构直接打包快照，避免频繁关系型表迁移带来的快照字段丢失。
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AgentSettings(Base):
    """
    智能体高级运行时配置扩展。

    🎯 意图与工程大局观：
        补充智能体在工作流中的变量、推荐词、工具调度策略等松散元配置。
    """
    __tablename__ = "agent_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), unique=True, index=True)
    suggested_questions: Mapped[list] = mapped_column(JSON, default=list)
    variables: Mapped[list] = mapped_column(JSON, default=list)
    memory: Mapped[dict] = mapped_column(JSON, default=dict)
    rag: Mapped[dict] = mapped_column(JSON, default=dict)
    tool_policy: Mapped[dict] = mapped_column(JSON, default=dict)
    query_understanding: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class AgentMemoryProfile(Base):
    """
    智能体长效上下文记忆实体。

    🎯 意图与工程大局观：
        配合 `memory.py` 实现用户-智能体级别的长效记忆持久化。
        `summary` 存储阶段性压缩后的对话摘要。
        `facts` 存储结构化的语义事实清单。
    """
    __tablename__ = "agent_memory_profiles"
    # 🛡️ 唯一性约束：特定工作区内、特定用户与特定智能体之间只能拥有一份记忆档案。
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", "agent_id", name="uq_agent_memory_profile_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    facts: Mapped[list] = mapped_column(JSON, default=list)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class PromptTemplate(Base):
    """
    Prompt 模板管理。
    """
    __tablename__ = "prompt_templates"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", "title", name="uq_prompt_templates_owner_title"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(80), default="general")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class ModelConfig(Base):
    """
    平台系统级预设模型定义。
    """
    __tablename__ = "model_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(80), default="openai-compatible")
    model_name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    supports_text: Mapped[bool] = mapped_column(Boolean, default=True)
    supports_image: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_document: Mapped[bool] = mapped_column(Boolean, default=True)
    supports_reasoning: Mapped[bool] = mapped_column(Boolean, default=False)
    reasoning_type: Mapped[str] = mapped_column(String(20), default="none")
    reasoning_label: Mapped[str] = mapped_column(String(80), default="不支持")
    max_context: Mapped[int] = mapped_column(Integer, default=8192)
    default_temperature: Mapped[float] = mapped_column(Float, default=0.4)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Upload(Base):
    """
    文件上传与提取元数据。

    🎯 意图与工程大局观：
        为多模态 Agent 和 RAG 系统提供前置输入支撑。
        支持提取文件内的纯文本数据 `text` 字段供大模型检索或直接阅读。
    """
    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(30))  # 'document' / 'image' / 'audio' / etc.
    # data_url 存储用于多模态渲染的 Base64 编码数据。
    # ⚡ 边界与性能思考：长文本和图像 data_url 对数据库 I/O 比较重，大文件上传在生产环境应建议存储到对象存储（S3/OSS）并在此处保存 URL 引用。
    data_url: Mapped[str] = mapped_column(Text, default="")
    # 对象存储中的对象键；非空表示文件存在对象库（此时 data_url 为空，按需取回转 base64）。
    storage_key: Mapped[str] = mapped_column(String(255), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class KnowledgeBase(Base):
    """
    RAG 知识库主体实体。
    """
    __tablename__ = "knowledge_bases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AgentKnowledgeBase(Base):
    """
    智能体与知识库的多对多映射表（带有唯一性约束）。
    """
    __tablename__ = "agent_knowledge_bases"
    __table_args__ = (UniqueConstraint("agent_id", "knowledge_base_id", name="uq_agent_kb"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)


class KnowledgeDocument(Base):
    """
    知识库上传的底层文档元数据表。

    🎯 意图与工程大局观：
        描述上传到具体知识库的文档分块数（chunk_count）以及向量化解析状态（status）。
    """
    __tablename__ = "knowledge_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    knowledge_base_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255), default="")
    content_type: Mapped[str] = mapped_column(String(120))
    source_type: Mapped[str] = mapped_column(String(20), default="text")
    text: Mapped[str] = mapped_column(LongText, default="")
    text_preview: Mapped[str] = mapped_column(Text, default="")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="uploaded")  # 'uploaded' / 'parsing' / 'success' / 'failed'
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    segment_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ingestion_log: Mapped[list | None] = mapped_column(JSON, nullable=True)



class KnowledgeChunk(Base):
    """
    知识库文档分块明细表。

    🎯 意图与工程大局观：
        RAG 混合检索中的“数据库召回映射源”。
        当在向量数据库中检索到 `vector_id` 后，通过其反查此处的 `text` 详情。
    """
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(LongText)
    # ⚡ 边界与性能思考：vector_id 对应 Milvus 等外部向量库的主键 UUID，做唯一索引方便快速反查。
    vector_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    
    # 🧠 魔鬼数字与前沿技术参数：
    # parent_id 和 chunk_id 用于前瞻性支持 Parent-Child 两阶段检索（多细粒度子块召回长父块逻辑）。
    parent_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    chunk_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    
    title: Mapped[str] = mapped_column(String(255), default="")
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str] = mapped_column(String(255), default="")
    content_hash: Mapped[str] = mapped_column(String(80), default="", index=True)
    embedding_model: Mapped[str] = mapped_column(String(160), default="")
    embedding_dimension: Mapped[int] = mapped_column(Integer, default=0)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


class KnowledgeParentChunk(Base):
    """父块表：存 parent-child 分段的父块全文，仅供检索后扩展上下文，不参与检索（不 embedding）。"""
    __tablename__ = "knowledge_parent_chunks"
    __table_args__ = (UniqueConstraint("document_id", "parent_id", name="uq_parent_chunk_doc_parent"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str] = mapped_column(String(120), index=True)
    text: Mapped[str] = mapped_column(LongText)
    content_hash: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Tool(Base):
    """
    工具定义表。

    🎯 意图与工程大局观：
        存储外部 API / HTTP 执行插件的 Schema 描述。
        当 `type` 为 "builtin" 时是平台预设硬编码工具；
        为 "http" 时，利用 `url`、`method`、`headers_schema` 等运行时构造发起动态沙箱调用。
    """
    __tablename__ = "tools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(40), default="builtin")  # 'builtin' / 'http' / etc.
    name: Mapped[str] = mapped_column(String(120), index=True)
    label: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    # 符合 JSON Schema 格式的工具输入参数协议声明，大模型根据该声明生成参数。
    schema: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # ── 自定义 HTTP 工具专用字段 ────────────────────────────
    method: Mapped[str] = mapped_column(String(12), default="GET")
    url: Mapped[str] = mapped_column(Text, default="")
    headers_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    query_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    body_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    auth_type: Mapped[str] = mapped_column(String(40), default="none")  # 'none' / 'api_key' / etc.
    auth_header_name: Mapped[str] = mapped_column(String(120), default="Authorization")
    auth_query_name: Mapped[str] = mapped_column(String(120), default="")
    # 🛡️ 安全设计：HTTP Auth 密钥必须经由对称加密加密存储在此，解密后注入运行时。
    encrypted_secret: Mapped[str] = mapped_column(Text, default="")
    
    # 🧠 魔鬼数字与前沿技术参数：
    # response_path 用于通过 JSONPath（如 '$.data.result'）自动解析接口返回值，截断无用元数据以压缩输入 Token 占用。
    response_path: Mapped[str] = mapped_column(String(200), default="$")
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=10)
    search_options: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class AgentTool(Base):
    """
    智能体与工具映射表。
    """
    __tablename__ = "agent_tools"
    __table_args__ = (UniqueConstraint("agent_id", "tool_id", name="uq_agent_tool"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    tool_id: Mapped[int] = mapped_column(ForeignKey("tools.id", ondelete="CASCADE"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AgentAgentBinding(Base):
    """
    智能体与子智能体绑定(supervisor → 已发布子 agent)。

    🎯 绑定后,子 agent 在 supervisor 运行时被包装成 type="agent" 工具,
    由 LLM 在 ReAct 循环里按需调用,实现多智能体协作(supervisor/orchestrator 模式)。
    """
    __tablename__ = "agent_agent_bindings"
    __table_args__ = (UniqueConstraint("agent_id", "target_agent_id", name="uq_agent_agent_binding"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    target_agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class McpServer(Base):
    """
    MCP(Model Context Protocol)server 配置。

    🎯 平台作为 MCP client 接入用户配置的 MCP server(stdio transport),
    把其工具暴露给绑定它的 agent 调用,实现工具生态扩展(无需逐个手写 HTTP 工具)。
    """
    __tablename__ = "mcp_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    transport: Mapped[str] = mapped_column(String(20), default="stdio")
    # command: 启动 server 的命令列表,如 ["python", "-m", "mymcp"] 或 ["npx", "server.js"]
    command: Mapped[list] = mapped_column(JSON, default=list)
    env: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class AgentMcpBinding(Base):
    """
    智能体与 MCP server 绑定表。
    """
    __tablename__ = "agent_mcp_bindings"
    __table_args__ = (UniqueConstraint("agent_id", "mcp_server_id", name="uq_agent_mcp_server"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    mcp_server_id: Mapped[int] = mapped_column(ForeignKey("mcp_servers.id", ondelete="CASCADE"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class WorkflowDefinition(Base):
    """
    工作流节点定义实体。
    """
    __tablename__ = "workflow_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), unique=True, index=True)
    nodes: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class Session(Base):
    """
    智能体对话会话（Chat Session）模型。
    """
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    is_debug: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="调试会话标记")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class Message(Base):
    """
    对话历史消息模型。
    """
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # 'user' / 'assistant'
    content: Mapped[str] = mapped_column(LongText)
    sources: Mapped[list] = mapped_column(JSON, default=list)  # 用于回显大模型回答的知识库召回引用
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Run(Base):
    """
    工作流引擎单次执行追踪（Run Trace）模型。
    """
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # 'running' / 'succeeded' / 'failed'
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RunStep(Base):
    """
    单次执行轨迹中的步骤节点（Run Step Detail）。

    🎯 意图与工程大局观：
        详尽记录 Workflow 中各个节点（如 Start -> Knowledge -> Tool -> LLM -> Answer）执行时的输入与输出，
        为平台提供强大的节点级 Trace 可视化展示。
    """
    __tablename__ = "run_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(String(80))
    node_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class SessionMemory(Base):
    """
    会话短期记忆缓存模型。

    🎯 意图与工程大局观：
        为多轮对话提供极其高效的记忆滚动压缩。
        `summary` 存储了一段序列化的对话历史快照，并在每次运行后按窗口截断或压缩。
    """
    __tablename__ = "session_memory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), unique=True, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class Feedback(Base):
    """
    智能体回答反馈（赞/踩/评）。
    """
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    rating: Mapped[str] = mapped_column(String(20))  # 'like' / 'dislike'
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
