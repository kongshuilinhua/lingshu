"""
灵枢 Agent 平台 —— 全局配置中心模块。

🎯 架构角色：
    本模块是整个平台的「单一事实来源」(Single Source of Truth)，所有子系统
    （LLM 网关、RAG 检索引擎、向量数据库、安全层、Web 搜索）的可调参数
    都集中在此通过环境变量注入，避免硬编码散落在业务逻辑中。

    使用 pydantic-settings 实现「环境变量 → 强类型 Python 对象」的自动映射，
    并通过 @lru_cache 保证全局唯一实例（进程级单例）。
"""

from functools import lru_cache
from pathlib import Path
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.ssl_bootstrap import ensure_valid_ssl_cert_file

# 🛡️ 进程启动期自愈：修复 conda 注入但实际缺失的 SSL_CERT_FILE，否则所有 httpx
# 工具（arxiv/wikipedia/weather/web_search…）会在 client 初始化时整体崩溃。
# config 模块被几乎所有子系统最早导入，可保证在第一次 httpx 调用前生效。
ensure_valid_ssl_cert_file()


class Settings(BaseSettings):
    """
    全局配置类，承载平台所有可调参数。

    🎯 设计意图：
        - 通过 env_file 支持 .env 和 .env.local 双层覆盖，方便本地开发与 CI 环境隔离
        - extra="ignore" 忽略未声明的环境变量，防止拼写错误的变量导致启动崩溃
        - 所有敏感字段（API Key、JWT Secret）通过 alias 映射大写环境变量名，符合 12-Factor 规范
    """

    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), env_file_encoding="utf-8", extra="ignore")

    # ── 应用基础信息 ────────────────────────────────────────────
    app_name: str = "Lingshu Agent"
    app_version: str = "0.1.0"
    deployment_mode: str = Field(default="development", alias="LINGSHU_DEPLOYMENT_MODE")

    # ── 认证与安全 ──────────────────────────────────────────────
    # 🛡️ JWT 密钥：生产环境必须替换默认值，否则任何人都能伪造令牌
    jwt_secret: str = Field(default="change-me-in-production", alias="JWT_SECRET")
    # 🧠 HS256 对称签名算法：性能好、适合单体部署；若需微服务间验证，应切换为 RS256 非对称算法
    jwt_algorithm: str = "HS256"
    # ⚡ 令牌有效期 1440 分钟（24 小时），平衡安全性与用户体验；缩短可降低令牌泄漏风险
    access_token_minutes: int = 60 * 24
    # 🛡️ API 密钥加密密钥：用于 Fernet 对称加密存储用户的第三方 API Key，与 JWT Secret 解耦防止单点泄漏
    api_key_encryption_key: str | None = Field(default=None, alias="API_KEY_ENCRYPTION_KEY")
    # 邀请制注册开关：关闭时任何人可自由注册
    invite_api_enabled: bool = Field(default=False, alias="INVITE_API_ENABLED")
    # 🛡️ CORS 白名单：限制前端来源，防止跨站请求伪造
    cors_origins: str = Field(default="http://127.0.0.1:5174,http://localhost:5174", alias="CORS_ORIGINS")

    # ── 数据库 ──────────────────────────────────────────────────
    database_url: str = Field(
        default="mysql+pymysql://lingshu:lingshu@192.168.150.101:3306/lingshu_agent",
        alias="DATABASE_URL",
    )
    # Redis 用于 RAG 检索结果缓存，非必须依赖（不配置则静默跳过缓存）
    redis_url: str | None = Field(default=None, alias="REDIS_URL")

    # ── 异步任务队列 (Celery) ───────────────────────────────────
    # 文档入库 / 重建索引 / 会话记忆压缩的执行后端。关闭（默认）或未安装 celery 时，
    # 自动回退到 FastAPI BackgroundTasks（进程内执行，进程重启会丢失在途任务）。
    celery_enabled: bool = Field(default=False, alias="CELERY_ENABLED")
    # broker / result backend 留空则复用 redis_url，避免重复配置。
    celery_broker_url: str | None = Field(default=None, alias="CELERY_BROKER_URL")
    celery_result_backend: str | None = Field(default=None, alias="CELERY_RESULT_BACKEND")

    # 熔断器分布式状态：开启后模型熔断的「失败计数 + 开断状态」存 Redis，多 worker 共享一致；
    # 关闭（默认）或 Redis 不可用时退回进程内三态熔断器。
    circuit_breaker_distributed: bool = Field(default=False, alias="CIRCUIT_BREAKER_DISTRIBUTED")

    # ── 对象存储（上传文件，软依赖） ────────────────────────────
    # backend: "none"（默认，文件 base64 直存 DB，现状）/ "minio"。
    # 不配置或 minio 未安装时自动回退 DB 内联，单机零改动。
    storage_backend: str = Field(default="none", alias="STORAGE_BACKEND")
    storage_endpoint: str | None = Field(default=None, alias="STORAGE_ENDPOINT")  # host:port，不含 scheme
    storage_bucket: str = Field(default="lingshu-uploads", alias="STORAGE_BUCKET")
    storage_access_key: str | None = Field(default=None, alias="STORAGE_ACCESS_KEY")
    storage_secret_key: str | None = Field(default=None, alias="STORAGE_SECRET_KEY")
    storage_secure: bool = Field(default=False, alias="STORAGE_SECURE")

    # ── LLM 供应商配置 ─────────────────────────────────────────
    # 🎯 多供应商路由设计：默认走 SiliconFlow 的 OpenAI 兼容接口，
    #    也可无缝切换至 DashScope/DeepSeek 或任何 OpenAI 兼容端点
    openai_api_base: str = Field(default="https://api.siliconflow.cn/v1", alias="OPENAI_API_BASE")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    # 🧠 默认聊天模型：deepseek-ai/DeepSeek-V4-Flash，性价比高（¥1/M 输入）
    openai_model: str = Field(default="deepseek-ai/DeepSeek-V4-Flash", alias="OPENAI_MODEL")
    # 🧠 Embedding 模型：BAAI/bge-m3，多语言长上下文(8192)、1024 维，SiliconFlow 免费款
    openai_embedding_model: str = Field(default="BAAI/bge-m3", alias="OPENAI_EMBEDDING_MODEL")
    # 🛡️ 嵌入批量上限：DashScope text-embedding-v3 单次最多 10 条，超出会 400 InvalidParameter。
    # 其它供应商（如 OpenAI 可达 2048）可通过环境变量调大。
    embedding_batch_size: int = Field(default=10, alias="EMBEDDING_BATCH_SIZE")
    dashscope_api_key: str | None = Field(default=None, alias="DASHSCOPE_API_KEY")
    deepseek_api_base: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_API_BASE")
    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    deepseek_model: str = Field(default="deepseek-chat", alias="DEEPSEEK_MODEL")
    # 独立的 Embedding / Rerank 端点，允许将嵌入服务与聊天服务部署在不同集群
    embedding_api_base: str | None = Field(default=None, alias="EMBEDDING_API_BASE")
    embedding_api_key: str | None = Field(default=None, alias="EMBEDDING_API_KEY")
    rerank_api_base: str | None = Field(default=None, alias="RERANK_API_BASE")
    rerank_api_key: str | None = Field(default=None, alias="RERANK_API_KEY")
    health_model_probe_timeout_seconds: int = Field(default=30, ge=1, le=120, alias="HEALTH_MODEL_PROBE_TIMEOUT_SECONDS")
    # 健康检查是否探测模型端点连通性
    health_model_probe_enabled: bool = Field(default=True, alias="HEALTH_MODEL_PROBE_ENABLED")
    # 🛡️ Mock LLM 模式：测试环境下跳过真实 API 调用，返回确定性伪造结果，保证 CI 稳定性
    mock_llm: bool = Field(default=False, validation_alias=AliasChoices("LINGSHU_MOCK_LLM", "SWEEPER_MOCK_LLM"))
    # 🛡️ 聊天补全 max_tokens 兜底：防止混合推理模型思考跑飞导致单次请求生成无上限（0 表示不下发该参数）
    llm_max_tokens: int = Field(default=8192, ge=0, alias="LLM_MAX_TOKENS")
    # 查询理解解析器：默认保留原生 JSON 解析；可选 LangChain 结构化输出
    qu_parser: str = Field(default="native", alias="QU_PARSER")
    # 跨语言查询翻译：中文查询→英文翻译喂给 BM25（英文文档），Dense/Rerank 不受影响
    translate_query_enabled: bool = Field(default=True, alias="LINGSHU_TRANSLATE_QUERY")
    # 翻译用模型（None 复用主对话模型；建议配成免费/便宜的小模型如 Qwen3-8B）
    translate_model: str | None = Field(default=None, alias="LINGSHU_TRANSLATE_MODEL")

    # ── LangSmith 可观测性（默认关闭，不产生外部 trace） ────────
    langsmith_tracing: bool = Field(default=False, alias="LANGSMITH_TRACING")
    langsmith_api_key: str | None = Field(default=None, alias="LANGSMITH_API_KEY")
    langsmith_project: str = Field(default="lingshu-agent", alias="LANGSMITH_PROJECT")

    # LangChain DocumentLoaders 扩展文档类型，默认关闭以保留原生上传行为
    ingest_langchain_loaders: bool = Field(default=False, alias="INGEST_LANGCHAIN_LOADERS")

    # ── 向量数据库 (Milvus) ─────────────────────────────────────
    milvus_uri: str = Field(default="http://192.168.150.101:19530", alias="MILVUS_URI")
    milvus_token: str | None = Field(default=None, alias="MILVUS_TOKEN")
    # 🧠 Collection 命名约定：一个平台实例对应一个 collection，通过 metadata 过滤实现多租户隔离
    milvus_collection: str = Field(default="lingshu_chunks", alias="MILVUS_COLLECTION")
    # 向量维度：留空则自动从首次 embed 结果推断，设置后可强制固定维度
    milvus_dimension: int | None = Field(default=None, alias="MILVUS_DIMENSION")
    # 🛡️ 向量后端选择："memory" 为开发模式（纯内存，重启丢失）；"milvus" 为生产模式
    vector_backend: str = Field(default="memory", validation_alias=AliasChoices("LINGSHU_VECTOR_BACKEND", "SWEEPER_VECTOR_BACKEND"))

    # ── RAG 混合检索参数 ────────────────────────────────────────
    # 🧠 以下参数控制 Dense + BM25 + RRF 三路混合检索管线的行为

    # top_k: 最终返回给 LLM 的知识片段数量
    # ⚡ 值过高 → 注入过多上下文导致 token 浪费和注意力稀释；值过低 → 可能遗漏关键证据
    rag_top_k: int = Field(default=4, alias="RAG_TOP_K")

    # dense_top_k: 向量相似度检索（Dense Retrieval）的候选数量
    # 🧠 这是第一路检索通道，通过 Embedding 余弦相似度召回语义相关片段
    # ⚡ 设为 12 是因为后续还有 RRF 融合和 Rerank 精排，需要足够大的候选池
    rag_dense_top_k: int = Field(default=12, alias="RAG_DENSE_TOP_K")

    # bm25_top_k: BM25 稀疏检索的候选数量
    # 🧠 第二路检索通道，基于词频-逆文档频率的经典信息检索算法，擅长精确关键词匹配
    # ⚡ 与 dense_top_k 对称设置，保证两路检索在 RRF 融合时贡献均衡
    rag_bm25_top_k: int = Field(default=12, alias="RAG_BM25_TOP_K")

    # rrf_k: Reciprocal Rank Fusion 融合常数
    # 🧠 RRF 公式：score = Σ 1/(k + rank_i)，k 越大则各路排名差异的影响越平滑
    # ⚡ k=60 是学术界常用默认值（来自 Cormack et al. 2009），适用于大多数场景
    #    k 过小 → 排名靠前的结果权重过大，接近 winner-take-all；k 过大 → 趋近于均匀分配
    rag_rrf_k: int = Field(default=60, alias="RAG_RRF_K")

    # Rerank 重排序：使用 Cross-Encoder 或 API 对 RRF 融合结果做精排
    rag_rerank_enabled: bool = Field(default=True, alias="RAG_RERANK_ENABLED")
    rag_rerank_model: str = Field(default="BAAI/bge-reranker-v2-m3", alias="RAG_RERANK_MODEL")
    # rerank_top_n: 精排后保留的候选数，通常大于最终 top_k 以给后续过滤留余量
    rag_rerank_top_n: int = Field(default=6, alias="RAG_RERANK_TOP_N")

    # RAG 缓存策略：相同查询 + 相同知识库版本 → 命中缓存，避免重复 Embedding + 检索开销
    rag_cache_enabled: bool = Field(default=True, alias="RAG_CACHE_ENABLED")
    # 🧠 缓存 TTL 3600 秒（1 小时）：平衡实时性与性能，知识库更新会通过版本哈希自动失效
    rag_cache_ttl_seconds: int = Field(default=3600, alias="RAG_CACHE_TTL_SECONDS")
    # ── Embedding 缓存 ──────────────────────────────────────────
    # 🧠 embedding 是「确定性 + 走外部 API（最贵）」的操作，且不随知识库文档增删而失效，
    #    命中率远高于「整条检索结果」缓存，是缓存分层里性价比最高的一层。按 (model, 文本) sha256 缓存。
    embedding_cache_enabled: bool = Field(default=True, alias="EMBEDDING_CACHE_ENABLED")
    # 🧠 TTL 默认 7 天：embedding 不会因文档变更而改变，TTL 仅为给 Redis 内存占用设上限。
    embedding_cache_ttl_seconds: int = Field(default=7 * 24 * 3600, alias="EMBEDDING_CACHE_TTL_SECONDS")
    # 🛡️ 无证据时拒绝回答：防止 LLM 在知识库无相关内容时产生幻觉
    rag_refuse_when_no_evidence: bool = Field(default=True, alias="RAG_REFUSE_WHEN_NO_EVIDENCE")
    # 🎯 父块扩展（small-to-big 检索）：用 child 小块精准命中、排序，最终喂给 LLM 时换成对应父块全文。
    # 检索精度与上下文完整性兼得；无父块（旧数据/非层级切分）自动回退到 child 全文。
    rag_parent_expansion: bool = Field(default=True, alias="RAG_PARENT_EXPANSION")
    # LangGraph CRAG-lite 自纠检索，默认关闭以保留原生单趟检索行为
    rag_self_correct: bool = Field(default=False, alias="RAG_SELF_CORRECT")
    rag_self_correct_max_rounds: int = Field(default=2, alias="RAG_SELF_CORRECT_MAX_ROUNDS")

    # ── 文档切分（auto/默认 parent-child）参数 ────────────────────────
    # 🧠 长度单位为 token（cl100k_base 估算），而非字符：中英混排时块长度更稳定，
    #    避免「520 字符」在中文≈900 token、英文≈130 token 的剧烈漂移导致 embedding 质量不稳。
    # ⚡ child 小块用于精准向量/BM25 命中与排序；parent 大块在 small-to-big 扩展时喂给 LLM。
    rag_chunk_parent_tokens: int = Field(default=768, alias="RAG_CHUNK_PARENT_TOKENS")
    rag_chunk_child_tokens: int = Field(default=256, alias="RAG_CHUNK_CHILD_TOKENS")
    # 🧠 重叠保证跨边界语义不被切断；parent 也加 overlap（旧实现 parent 无重叠，答案跨父块时会断裂）。
    rag_chunk_parent_overlap_tokens: int = Field(default=96, alias="RAG_CHUNK_PARENT_OVERLAP_TOKENS")
    rag_chunk_child_overlap_tokens: int = Field(default=48, alias="RAG_CHUNK_CHILD_OVERLAP_TOKENS")
    # 🎯 上下文增强（contextual retrieval）：child 向量化前前缀拼「文档标题 + 章节面包屑」，
    #    提升召回（裸小块常缺主语/所属章节）；只增强送入 embedding 的文本，落库/展示正文保持干净。
    rag_chunk_contextual_embed: bool = Field(default=True, alias="RAG_CHUNK_CONTEXTUAL_EMBED")
    # CSV 结构化分段：按 token 预算动态决定每个 child/parent 容纳的数据行数，适配宽表/窄表。
    rag_chunk_csv_child_tokens: int = Field(default=256, alias="RAG_CHUNK_CSV_CHILD_TOKENS")
    rag_chunk_csv_parent_tokens: int = Field(default=1024, alias="RAG_CHUNK_CSV_PARENT_TOKENS")

    # ── Web 搜索 ────────────────────────────────────────────────
    web_search_enabled: bool = Field(default=True, alias="WEB_SEARCH_ENABLED")
    # 🧠 使用 DuckDuckGo HTML 版本：免费、无需 API Key、无速率限制，但依赖 HTML 解析稳定性
    web_search_provider: str = Field(default="duckduckgo_html", alias="WEB_SEARCH_PROVIDER")
    web_search_top_k: int = Field(default=5, alias="WEB_SEARCH_TOP_K")
    # ⚡ 搜索超时 8 秒：DDG 在中国大陆可能较慢，过短会频繁超时，过长会阻塞用户请求
    web_search_timeout_seconds: int = Field(default=8, alias="WEB_SEARCH_TIMEOUT_SECONDS")
    # 🛡️ 响应体大小上限 512KB：防止恶意或异常网页导致内存暴涨
    web_search_max_response_bytes: int = Field(default=512 * 1024, alias="WEB_SEARCH_MAX_RESPONSE_BYTES")
    web_search_user_agent: str = Field(default="LingshuAgent/0.1 (+https://local.lingshu.agent)", alias="WEB_SEARCH_USER_AGENT")

    # ── 文件上传 ────────────────────────────────────────────────
    # 🛡️ 上传大小限制 8MB：防止超大文件拖垮文本提取和向量化流程
    upload_max_bytes: int = Field(default=30 * 1024 * 1024, alias="UPLOAD_MAX_BYTES")

    # ── 会话记忆摘要 ──────────────────────────────────────────
    # 🎯 会话记忆摘要配置
    memory_summary_enabled: bool = Field(default=True, alias="MEMORY_SUMMARY_ENABLED")
    # 🧠 摘要最大字符数：过长会突破模型上下文；800 字符可覆盖 3-5 轮对话的关键信息
    memory_summary_max_chars: int = Field(default=800, alias="MEMORY_SUMMARY_MAX_CHARS")
    # 🧠 压缩后保留的最近轮数
    memory_keep_recent_turns: int = Field(default=3, alias="MEMORY_KEEP_RECENT_TURNS")
    # 🧠 摘要使用的独立廉价模型
    memory_summary_model: str | None = Field(default=None, alias="MEMORY_SUMMARY_MODEL")
    # 🧠 会话记忆总 token 预算与最近窗口 token 预算
    memory_token_budget: int = Field(default=3000, alias="MEMORY_TOKEN_BUDGET")
    memory_recent_token_budget: int = Field(default=1500, alias="MEMORY_RECENT_TOKEN_BUDGET")
    # 🧠 长期记忆自动抽取开关与向量召回 Top-K
    memory_long_term_extract_enabled: bool = Field(default=False, alias="MEMORY_LONG_TERM_EXTRACT")
    memory_recall_top_k: int = Field(default=5, alias="MEMORY_RECALL_TOP_K")
    # 🧠 facts 进程内召回的余弦相似度阈值：低于此分的 fact 视为与本轮 query 无关、不召回。
    #    0.5 在 mock embedding 噪声(~0.32)之上、精确命中(1.0)之下，生产用实嵌入时相关 fact 通常 0.5~0.8。
    memory_recall_min_score: float = Field(default=0.5, alias="MEMORY_RECALL_MIN_SCORE")

    # ── 存储路径 ────────────────────────────────────────────────
    data_dir: Path = Path("data")
    upload_dir: Path = Path("storage/uploads")

    @field_validator("milvus_dimension", mode="before")
    @classmethod
    def empty_dimension_is_none(cls, value):
        """
        🛡️ 防御性处理：环境变量传入空字符串时转为 None，
        避免 int("") 抛出 ValueError 导致启动失败。
        """
        if value == "":
            return None
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        """将逗号分隔的 CORS 来源字符串解析为列表，过滤空白项。"""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.deployment_mode.strip().lower() in {"prod", "production"}

    def production_readiness_issues(self) -> list[str]:
        """Return blocking configuration issues when running in production mode."""
        if not self.is_production:
            return []
        issues: list[str] = []
        if self.jwt_secret == "change-me-in-production" or len(self.jwt_secret.strip()) < 32:
            issues.append("JWT_SECRET must be a strong random value of at least 32 characters.")
        if not self.api_key_encryption_key or len(self.api_key_encryption_key.strip()) < 32:
            issues.append("API_KEY_ENCRYPTION_KEY must be configured before storing user model keys or tool secrets.")
        if self.mock_llm:
            issues.append("LINGSHU_MOCK_LLM must be false in production.")
        if self.vector_backend.strip().lower() == "memory":
            issues.append("LINGSHU_VECTOR_BACKEND=memory is only suitable for local development.")
        if not self.redis_url:
            issues.append("REDIS_URL is required in production for cache, rate limit, job state, and Celery.")
        if not self.celery_enabled:
            issues.append("CELERY_ENABLED=true is required in production so indexing and memory jobs survive API restarts.")
        if "*" in self.cors_origin_list:
            issues.append("CORS_ORIGINS must not contain '*' when credentials are enabled.")
        if "://lingshu:lingshu@" in self.database_url:
            issues.append("DATABASE_URL must not use the default development database password in production.")
        if self.storage_backend.strip().lower() == "minio" and (
            self.storage_access_key in {None, "", "minioadmin"} or self.storage_secret_key in {None, "", "minioadmin123"}
        ):
            issues.append("MinIO credentials must be changed from development defaults in production.")
        return issues


@lru_cache
def get_settings() -> Settings:
    """
    全局配置单例工厂。

    🎯 使用 @lru_cache 保证整个进程生命周期内只解析一次环境变量，
    后续调用直接返回缓存实例，零开销。

    ⚡ 注意：@lru_cache 意味着运行时修改环境变量不会生效，
    这是有意为之——配置应在启动时确定，运行时不可变。
    """
    return Settings()
