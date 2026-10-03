from pydantic import BaseModel, EmailStr, Field


class AgentRagConfig(BaseModel):
    """
    智能体 RAG 混合检索引擎参数约束 Schema。

    🎯 意图与工程大局观：
        为 RAG 知识检索节点提供严格的调优边界规约，防止低效参数拖垮向量库或模型。
    
    🧠 魔鬼数字与前沿技术参数：
        - `top_k`: 最终召回结果上限。强限制在 `[1, 20]`，防止大模型处理超长检索片段爆掉 Context。
        - `dense_top_k` 和 `bm25_top_k`: 多路召回初始深度。强限制在 `[1, 50]`，确保兼顾高召回率与计算开销。
        - `rrf_k`: 互惠排名融合常数。限制在 `[1, 200]`，默认 60，调谐稠密与稀疏检索的权重比例。
        - `rerank_top_n`: 重排模型输出片段上限。限制在 `[1, 20]`。
    """
    enabled_by_default: bool = True
    top_k: int = Field(default=4, ge=1, le=20)
    dense_top_k: int | None = Field(default=None, ge=1, le=50)
    bm25_top_k: int | None = Field(default=None, ge=1, le=50)
    rrf_k: int | None = Field(default=None, ge=1, le=200)
    rerank_enabled: bool | None = None
    rerank_top_n: int | None = Field(default=None, ge=1, le=20)
    cache_enabled: bool | None = None
    refuse_when_no_evidence: bool | None = None


class AgentVariable(BaseModel):
    """
    智能体运行时用户上下文自定义变量 Schema。

    🛡️ 防御性编程：限制 `key` 最大 80 字符，`label` 最大 120 字符。
        强力拦截针对 Prompt 拼接段的爆内存以及提示词注入攻击。
    """
    key: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=120)
    type: str = "string"
    required: bool = False
    default_value: str | int | float | bool | None = None


class AgentMemoryConfig(BaseModel):
    """
    智能体长/短期会话记忆参数约束 Schema。

    🧠 魔鬼数字：`max_messages` 最多保存历史消息条数。
        强限制在 `[1, 100]`，防止记忆无限膨胀塞爆 Context，杜绝无限套娃带来的天价 Token 账单。
    """
    enabled: bool = False
    strategy: str = "session_summary"
    max_messages: int = Field(default=12, ge=1, le=100)


class MemoryProfileUpdateRequest(BaseModel):
    """长期画像/事实记忆更新载荷。"""
    enabled: bool | None = None
    summary: str | None = None
    facts: list[str] | None = None
    preferences: dict[str, object] | None = None


class AgentToolPolicy(BaseModel):
    """智能体工具路由策略，约束模型自动或指定执行特定工具集。"""
    mode: str = "auto"
    allowed_tool_names: list[str] = []


class RegisterRequest(BaseModel):
    """
    注册请求载荷。

    🛡️ 防御性编程：强制要求密码 `password` 最低 8 位，防暴力破译；用户名限制 120 字符以内。
    """
    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8)
    invite_token: str | None = None


class LoginRequest(BaseModel):
    """登录凭证验证载荷。"""
    email: EmailStr
    password: str


class UserProfileUpdateRequest(BaseModel):
    """
    用户个人资料更新载荷。

    🛡️ 防御性编程：强制 `avatar_url` 最大为 1,500,000 字符。
        允许前端直接上传 Base64 编码的微型头像，但严密防范超大 Base64 数据洪流撑爆服务端内存。
    """
    name: str | None = Field(default=None, min_length=1, max_length=120)
    avatar_url: str | None = Field(default=None, max_length=1_500_000)


class AgentCreateRequest(BaseModel):
    """
    创建智能体载荷契约（Agent 编排元数据底盘）。

    🛡️ 防御性编程：约束 name 最大 160 字符，温度限制在标准 0.4。
    """
    name: str = Field(min_length=1, max_length=160)
    avatar: str = "AI"
    description: str = ""
    opening_message: str = ""
    system_prompt: str = ""
    model_id: int | None = None
    user_model_config_id: int | None = None
    model: str | None = None
    temperature: float = 0.4
    knowledge_base_ids: list[int] = []
    tool_ids: list[int] = []
    suggested_questions: list[str] = []
    variables: list[AgentVariable] = []
    memory: AgentMemoryConfig = Field(default_factory=AgentMemoryConfig)
    rag: AgentRagConfig = Field(default_factory=AgentRagConfig)
    tool_policy: AgentToolPolicy = Field(default_factory=AgentToolPolicy)


class AgentUpdateRequest(BaseModel):
    """智能体更新契约载荷（全部参数皆可按需部分更新）。"""
    name: str | None = None
    avatar: str | None = None
    description: str | None = None
    opening_message: str | None = None
    system_prompt: str | None = None
    model_id: int | None = None
    user_model_config_id: int | None = None
    model: str | None = None
    temperature: float | None = None
    knowledge_base_ids: list[int] | None = None
    tool_ids: list[int] | None = None
    suggested_questions: list[str] | None = None
    variables: list[AgentVariable] | None = None
    memory: AgentMemoryConfig | None = None
    rag: AgentRagConfig | None = None
    tool_policy: AgentToolPolicy | None = None


class KnowledgeBaseCreateRequest(BaseModel):
    """创建知识库请求。"""
    name: str = Field(min_length=1, max_length=160)
    description: str = ""


class KnowledgeDocumentCreateRequest(BaseModel):
    """
    创建/导入知识库文档请求载荷。

    🛡️ 防御性编程：
        - `content_type` 强正则/长度校验，防止恶意构造头欺骗提取引擎。
        - `source_type` 强制限定在 `^(text|file)$` 中，杜绝未知类型导致下游未捕获异常泄露栈信息。
    """
    filename: str | None = Field(default=None, min_length=1, max_length=255)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    text: str | None = Field(default=None, min_length=1)
    content: str | None = Field(default=None, min_length=1)
    content_type: str = Field(default="text/plain", min_length=1, max_length=120)
    content_base64: str | None = Field(default=None, min_length=1)
    source_type: str = Field(default="text", pattern="^(text|file)$")
    # 首次上传即可指定切片策略（segment_mode/max_chunk_len/overlap_pct/hierarchy_level 等），
    # 不传则沿用默认（auto 父子滑窗）。
    segment_config: dict | None = Field(default=None)


class SessionUpdateRequest(BaseModel):
    """会话标题更新契约。"""
    title: str = Field(min_length=1, max_length=200)


class WorkflowUpdateRequest(BaseModel):
    """图工作流编排定义更新契约。"""
    nodes: list[dict]


class ChatRequest(BaseModel):
    """
    智能体统一对话请求核心契约（SSE 会话驱动的弹头）。

    🎯 意图与工程大局观：
        包含会话 ID、草稿/发布模式、深度思考、实时网页检索、运行时变量覆盖、上传附件及调试标记等。
        是人机长连接会话控制的最高参数锚点。
    """
    message: str = Field(min_length=1)
    session_id: int | None = None
    mode: str = "draft"
    rag_enabled: bool | None = None
    rag_options: AgentRagConfig | None = None
    thinking_enabled: bool | None = None
    search_enabled: bool | None = None
    variables: dict[str, str | int | float | bool | None] = {}
    attachments: list[dict] = []
    is_debug: bool = False


class ToolRequest(BaseModel):
    """
    创建自定义/第三方工具参数契约。

    🛡️ 防御性编程与大模型兜底：
        - `type` 强正则表达式检验，物理限制在 `^(builtin_search|http)$`，斩断了执行本地未知命令的后门风险。
        - `timeout_seconds`: 强制限制在 `[1, 30]` 秒。
          从底层切断了由于第三方慢 API 或挂死导致的平台工作流引擎阻塞与线程饥饿风险。
    """
    type: str = Field(default="http", pattern="^(builtin_search|http)$")
    name: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=160)
    description: str = ""
    enabled: bool = True
    method: str = "GET"
    url: str = ""
    headers_schema: dict = {}
    query_schema: dict = {}
    body_schema: dict = {}
    auth: dict = {}
    response_path: str = "$"
    timeout_seconds: int = Field(default=10, ge=1, le=30)
    search_options: dict = {}


class ToolUpdateRequest(BaseModel):
    """更新工具参数契约。"""
    type: str | None = Field(default=None, pattern="^(builtin_search|http)$")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    label: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    enabled: bool | None = None
    method: str | None = None
    url: str | None = None
    headers_schema: dict | None = None
    query_schema: dict | None = None
    body_schema: dict | None = None
    auth: dict | None = None
    response_path: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=30)
    search_options: dict | None = None


class ToolTestRequest(BaseModel):
    """在线调试执行第三方 API 工具时的参数沙箱验证载荷。"""
    input: dict = {}
    body: dict | list | str | int | float | bool | None = None


class PromptTemplateRequest(BaseModel):
    """
    创建 Prompt 模板参数契约。

    🛡️ 防御性编程：
        限制 `category` 最大 80 字符，从源头防止恶意长指令或溢出数据导致分类索引失效。
    """
    title: str = Field(min_length=1, max_length=160)
    description: str = ""
    content: str = Field(min_length=1)
    category: str = Field(default="general", min_length=1, max_length=80)
    tags: list[str] = []
    enabled: bool = True


class PromptTemplateUpdateRequest(BaseModel):
    """更新 Prompt 模板参数契约。"""
    title: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    content: str | None = Field(default=None, min_length=1)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    tags: list[str] | None = None
    enabled: bool | None = None


class PromptTemplateCopyBuiltinRequest(BaseModel):
    """复制系统内置模板请求契约。"""
    builtin_id: str = Field(min_length=1, max_length=120)
    title: str | None = Field(default=None, min_length=1, max_length=160)


class ModelConfigRequest(BaseModel):
    """
    添加全局系统内置默认大模型元数据配置契约。

    🛡️ 防御性编程与魔鬼数字：
        - `reasoning_type` 物理限定在 `^(native|prompt|none)$` 三大主流逻辑中，防非法入库。
        - `default_temperature` 强限制在 `[0, 2]` 区间，彻底规避由于温度参数非法（如负数或极端大值）造成大模型 API 执行雪崩崩溃。
    """
    provider: str = Field(default="openai-compatible", min_length=1, max_length=80)
    model_name: str = Field(min_length=1, max_length=160)
    display_name: str = Field(min_length=1, max_length=160)
    supports_text: bool = True
    supports_image: bool = False
    supports_document: bool = True
    supports_reasoning: bool = False
    reasoning_type: str = Field(default="none", pattern="^(native|prompt|none)$")
    reasoning_label: str = Field(default="不支持", max_length=80)
    max_context: int = Field(default=8192, ge=1)
    default_temperature: float = Field(default=0.4, ge=0, le=2)
    enabled: bool = True


class ModelConfigUpdateRequest(BaseModel):
    """更新大模型配置契约。"""
    provider: str | None = Field(default=None, min_length=1, max_length=80)
    model_name: str | None = Field(default=None, min_length=1, max_length=160)
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    supports_text: bool | None = None
    supports_image: bool | None = None
    supports_document: bool | None = None
    supports_reasoning: bool | None = None
    reasoning_type: str | None = Field(default=None, pattern="^(native|prompt|none)$")
    reasoning_label: str | None = Field(default=None, max_length=80)
    max_context: int | None = Field(default=None, ge=1)
    default_temperature: float | None = Field(default=None, ge=0, le=2)
    enabled: bool | None = None


class UserModelConfigRequest(BaseModel):
    """
    用户 Bring-Your-Own-Key (BYOK) 独享私有大模型连接参数配置契约。

    🛡️ 防御性编程与隐私保障：
        - 对 `api_key` 进行 Pydantic 过滤与最大值控制（限制 4096 字符，防溢出），
          在后续 CRUD 逻辑中强制脱敏校验，守护用户核心私钥。
        - `max_context` 上下文窗口底线限制为 1 个 Token 以上，防恶意除零计算报错。
    """
    display_name: str = Field(min_length=1, max_length=160)
    provider: str = Field(default="openai-compatible", min_length=1, max_length=80)
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str | None = Field(max_length=4096)
    chat_model: str = Field(min_length=1, max_length=160)
    supports_image: bool = False
    supports_document: bool = True
    supports_reasoning: bool = False
    reasoning_type: str = Field(default="none", pattern="^(native|prompt|none)$")
    reasoning_label: str = Field(default="不支持", max_length=80)
    max_context: int = Field(default=131072, ge=1)
    default_temperature: float = Field(default=0.4, ge=0, le=2)
    enabled: bool = True
    is_default: bool = False


class UserModelCapabilityTestRequest(UserModelConfigRequest):
    """私有大模型连通性与多模态/视觉推理支持实测请求载荷。"""
    detect_image: bool = False


class UserModelProbeModelsRequest(BaseModel):
    """拉取端点模型列表(GET /models)的请求契约:只需 base_url + api_key。"""
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str = Field(max_length=4096)


class UserModelConfigUpdateRequest(BaseModel):
    """更新私有大模型连接参数契约。"""
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    provider: str | None = Field(default=None, min_length=1, max_length=80)
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    api_key: str | None = Field(default=None, max_length=4096)
    chat_model: str | None = Field(default=None, min_length=1, max_length=160)
    supports_image: bool | None = None
    supports_document: bool | None = None
    supports_reasoning: bool | None = None
    reasoning_type: str | None = Field(default=None, pattern="^(native|prompt|none)$")
    reasoning_label: str | None = Field(default=None, max_length=80)
    max_context: int | None = Field(default=None, ge=1)
    default_temperature: float | None = Field(default=None, ge=0, le=2)
    enabled: bool | None = None
    is_default: bool | None = None


class UploadCreateRequest(BaseModel):
    """创建文件上传请求载荷。"""
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=120)
    content_base64: str = Field(min_length=1)


class InviteCreateRequest(BaseModel):
    """创建团队邀请请求载荷。"""
    email: EmailStr
    role: str = "user"


class InviteAcceptRequest(BaseModel):
    """接受团队邀请契约载荷。"""
    token: str


class FeedbackRequest(BaseModel):
    """对话最终结果评星与反馈建议请求契约（Rating 限制在 positive/negative/none 规范内）。"""
    rating: str = Field(pattern="^(positive|negative|none)$")
    comment: str = ""
