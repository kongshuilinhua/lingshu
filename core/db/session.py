import logging
import re

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from core.config import get_settings
from core.db.base import Base

logger = logging.getLogger(__name__)


def _safe_trigger_ddl(label: str, statements: list[str]) -> None:
    """
    在单个事务内执行一组 DDL（含 CREATE TRIGGER）。

    🛡️ 容错：受限环境（如无 SUPER 权限 + 开启 binlog 的 MySQL 会对 CREATE TRIGGER 抛 errno 1419）
        下静默跳过并告警，不阻断 init_db。触发器仅是 DB 层唯一约束的兜底，缺失不影响应用层逻辑与测试。
    """
    try:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
    except OperationalError as exc:
        logger.warning("跳过可选迁移 '%s'（数据库权限不足？触发器未创建）：%s", label, str(exc)[:200])


# 🎯 全局配置解析：加载平台配置单例
settings = get_settings()

# 🎯 数据库连接引擎引擎初始化
# future=True 启用 SQLAlchemy 2.0 兼容模式，保证查询接口的前向兼容性
#
# 🛡️ 连接存活性保障（仅对真实连接池的 driver 生效）：
#   - pool_pre_ping：借出连接前先探活。MySQL 的 wait_timeout 默认 8 小时会主动
#     切断空闲连接，而连接池并不知情、会继续持有这些死连接。典型症状是低峰期过后
#     第一个请求报 "MySQL server has gone away"。
#   - pool_recycle=3600：主动在 1 小时后回收连接，早于服务端超时，双重兜底。
#   SQLite 用的是 SingletonThreadPool/StaticPool，不接受这些参数，故按 driver 区分。
_engine_kwargs: dict = {"future": True}
if not settings.database_url.startswith("sqlite"):
    _engine_kwargs.update(pool_pre_ping=True, pool_recycle=3600)

engine = create_engine(settings.database_url, **_engine_kwargs)

# 🎯 会话工厂声明 (Thread-local Session Local)
# autoflush=False: 禁用自动提交缓冲区，防止未显式 commit 的修改提前落库，有利于事务边界控制
# autocommit=False: 显式事务控制，必须通过 db.commit() 提交，防止隐式事务泄漏
# future=True: 使用 2.0 时代的会话模式
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    """
    平台数据库自举初始化函数。

    🎯 工作流生命周期：
        1. 导入模型定义确保所有 SQLAlchemy 模型被 Base.metadata 收集。
        2. 基于 Base.metadata.create_all 执行物理建表（若表已存在则跳过）。
        3. 调用兼容性迁移脚本 `_run_compat_migrations` 处理数据库增量 schema 更新。
        4. 初始化自举内置的基础 LLM/Embeddings 模型配置。

    🛡️ 容错设计：
        使用 try-finally 确保即使内置模型配置自举失败（例如 API_KEY 配置问题），
        本地数据库 Session 也能被安全关闭，防止连接泄露。
    """
    from core.db import models  # noqa: F401
    from core.services.bootstrap import ensure_default_models

    Base.metadata.create_all(bind=engine)
    _run_compat_migrations()
    db = SessionLocal()
    try:
        ensure_default_models(db)
    finally:
        db.close()


def _run_compat_migrations() -> None:
    """
    🛡️ 防御性轻量级增量迁移机制。

    🎯 设计决策：
        为什么不使用 Alembic 而是通过原生 DDL 进行运行时 schema 校验？
        - 内部测试或私有化部署场景下，开发者/客户不需要配置和运行复杂的数据库迁移命令。
        - 运行时进行 schema 检测，实现“开箱即用”式自动升级，显著降低系统运维门槛。

    ⚡ 边界与性能思考：
        - 优先通过 `inspect(engine)` 加载元数据进行表结构判断，避免直接抛出 SQL 执行异常再捕捉，大幅减少 I/O 损耗。
        - 所有 DDL 操作包装在 `engine.begin()` 上下文管理器中，利用数据库事务保证 Schema 修改的原子性，防止中途断电导致表结构损坏。
    """
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return
    table_names = set(inspector.get_table_names())
    columns = {column["name"] for column in inspector.get_columns("users")}
    if "avatar_url" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE users ADD COLUMN avatar_url TEXT DEFAULT ''"))
    if "agents" in table_names:
        agent_columns = {column["name"] for column in inspector.get_columns("agents")}
        if "model_id" not in agent_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE agents ADD COLUMN model_id INTEGER"))
        if "user_model_config_id" not in agent_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE agents ADD COLUMN user_model_config_id INTEGER"))
        agent_indexes = {index["name"] for index in inspector.get_indexes("agents")}
        if "ix_agents_user_model_config_id" not in agent_indexes:
            with engine.begin() as connection:
                connection.execute(text("CREATE INDEX ix_agents_user_model_config_id ON agents (user_model_config_id)"))
        
        avatar_col = next((col for col in inspector.get_columns("agents") if col["name"] == "avatar"), None)
        if avatar_col and getattr(avatar_col["type"], "length", None) == 40:
            if engine.dialect.name == "postgresql":
                with engine.begin() as connection:
                    connection.execute(text("ALTER TABLE agents ALTER COLUMN avatar TYPE TEXT"))
            elif engine.dialect.name == "mysql":
                with engine.begin() as connection:
                    connection.execute(text("ALTER TABLE agents MODIFY COLUMN avatar TEXT"))
    if "user_model_configs" in table_names:
        _ensure_columns(
            "user_model_configs",
            {
                "supports_reasoning": "BOOLEAN DEFAULT false",
                "reasoning_type": "VARCHAR(20) DEFAULT 'none'",
                "reasoning_label": "VARCHAR(80) DEFAULT '不支持'",
            },
        )
        config_indexes = {index["name"] for index in inspector.get_indexes("user_model_configs")}
        if engine.dialect.name == "postgresql" and "ix_user_model_configs_one_default_per_user" not in config_indexes:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS ix_user_model_configs_one_default_per_user "
                        "ON user_model_configs (user_id) WHERE is_default = true"
                    )
                )
        elif engine.dialect.name == "mysql":
            user_config_columns = {col["name"] for col in inspector.get_columns("user_model_configs")}
            if "is_default_ukey" not in user_config_columns:
                _safe_trigger_ddl("user_model_configs.is_default_ukey", [
                    # 添加普通列（MySQL 生成列不能引用外键列，改用触发器维护）
                    "ALTER TABLE user_model_configs ADD COLUMN is_default_ukey VARCHAR(64) NULL",
                    # 唯一索引：MySQL 忽略 NULL，实现部分唯一约束效果
                    "CREATE UNIQUE INDEX uq_one_default_per_user ON user_model_configs (is_default_ukey)",
                    # 触发器：自动同步 is_default_ukey 的值
                    "CREATE TRIGGER trg_umc_default_ins "
                    "BEFORE INSERT ON user_model_configs FOR EACH ROW "
                    "SET NEW.is_default_ukey = IF(NEW.is_default = 1, CAST(NEW.user_id AS CHAR(64)), NULL)",
                    "CREATE TRIGGER trg_umc_default_upd "
                    "BEFORE UPDATE ON user_model_configs FOR EACH ROW "
                    "SET NEW.is_default_ukey = IF(NEW.is_default = 1, CAST(NEW.user_id AS CHAR(64)), NULL)",
                ])
    if "model_configs" in table_names:
        _ensure_columns(
            "model_configs",
            {
                "supports_reasoning": "BOOLEAN DEFAULT false",
                "reasoning_type": "VARCHAR(20) DEFAULT 'none'",
                "reasoning_label": "VARCHAR(80) DEFAULT '不支持'",
            },
        )
    if "agent_settings" in table_names:
        settings_columns = {column["name"] for column in inspector.get_columns("agent_settings")}
        if "rag" not in settings_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE agent_settings ADD COLUMN rag JSON DEFAULT '{}'"))
    if "tools" in table_names:
        _ensure_columns(
            "tools",
            {
                "workspace_id": "INTEGER",
                "user_id": "INTEGER",
                "type": "VARCHAR(40) DEFAULT 'builtin'",
                "method": "VARCHAR(12) DEFAULT 'GET'",
                "url": "TEXT DEFAULT ''",
                "headers_schema": "JSON DEFAULT '{}'",
                "query_schema": "JSON DEFAULT '{}'",
                "body_schema": "JSON DEFAULT '{}'",
                "auth_type": "VARCHAR(40) DEFAULT 'none'",
                "auth_header_name": "VARCHAR(120) DEFAULT 'Authorization'",
                "auth_query_name": "VARCHAR(120) DEFAULT ''",
                "encrypted_secret": "TEXT DEFAULT ''",
                "response_path": "VARCHAR(200) DEFAULT '$'",
                "timeout_seconds": "INTEGER DEFAULT 10",
                "search_options": "JSON DEFAULT '{}'",
                "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
                "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            },
        )
        tool_indexes = {index["name"] for index in inspector.get_indexes("tools")}
        if "ix_tools_workspace_id" not in tool_indexes:
            with engine.begin() as connection:
                connection.execute(text("CREATE INDEX ix_tools_workspace_id ON tools (workspace_id)"))
        if "ix_tools_user_id" not in tool_indexes:
            with engine.begin() as connection:
                connection.execute(text("CREATE INDEX ix_tools_user_id ON tools (user_id)"))
        if "ix_tools_name" not in tool_indexes:
            with engine.begin() as connection:
                connection.execute(text("CREATE INDEX ix_tools_name ON tools (name)"))
        # MySQL: partial unique indexes via triggers (generated columns can't reference FK columns)
        if engine.dialect.name == "mysql":
            tool_cols = {col["name"] for col in inspector.get_columns("tools")}
            if "global_name_ukey" not in tool_cols:
                _safe_trigger_ddl("tools.global_name_ukey", [
                    "ALTER TABLE tools ADD COLUMN global_name_ukey VARCHAR(200) NULL",
                    "CREATE UNIQUE INDEX uq_tools_global_name ON tools (global_name_ukey)",
                    "CREATE TRIGGER trg_tools_global_name_ins "
                    "BEFORE INSERT ON tools FOR EACH ROW "
                    "SET NEW.global_name_ukey = IF(NEW.workspace_id IS NULL AND NEW.user_id IS NULL, NEW.name, NULL)",
                    "CREATE TRIGGER trg_tools_global_name_upd "
                    "BEFORE UPDATE ON tools FOR EACH ROW "
                    "SET NEW.global_name_ukey = IF(NEW.workspace_id IS NULL AND NEW.user_id IS NULL, NEW.name, NULL)",
                ])
            if "owner_name_ukey" not in tool_cols:
                _safe_trigger_ddl("tools.owner_name_ukey", [
                    "ALTER TABLE tools ADD COLUMN owner_name_ukey VARCHAR(400) NULL",
                    "CREATE UNIQUE INDEX uq_tools_owner_name ON tools (owner_name_ukey)",
                    "CREATE TRIGGER trg_tools_owner_name_ins "
                    "BEFORE INSERT ON tools FOR EACH ROW "
                    "SET NEW.owner_name_ukey = IF(NEW.workspace_id IS NOT NULL AND NEW.user_id IS NOT NULL, "
                    "CONCAT(NEW.workspace_id, ':', NEW.user_id, ':', NEW.name), NULL)",
                    "CREATE TRIGGER trg_tools_owner_name_upd "
                    "BEFORE UPDATE ON tools FOR EACH ROW "
                    "SET NEW.owner_name_ukey = IF(NEW.workspace_id IS NOT NULL AND NEW.user_id IS NOT NULL, "
                    "CONCAT(NEW.workspace_id, ':', NEW.user_id, ':', NEW.name), NULL)",
                ])
    if "agent_tools" in table_names:
        _ensure_columns(
            "agent_tools",
            {
                "enabled": "BOOLEAN DEFAULT true",
                "config": "JSON DEFAULT '{}'",
                "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            },
        )
    if "agent_memory_profiles" in table_names:
        _ensure_columns(
            "agent_memory_profiles",
            {
                "workspace_id": "INTEGER",
                "user_id": "INTEGER",
                "agent_id": "INTEGER",
                "enabled": "BOOLEAN DEFAULT false",
                "summary": "TEXT DEFAULT ''",
                "facts": "JSON DEFAULT '[]'",
                "preferences": "JSON DEFAULT '{}'",
                "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
                "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            },
        )
        memory_indexes = {index["name"] for index in inspector.get_indexes("agent_memory_profiles")}
        if "ix_agent_memory_profiles_workspace_id" not in memory_indexes:
            with engine.begin() as connection:
                connection.execute(text("CREATE INDEX ix_agent_memory_profiles_workspace_id ON agent_memory_profiles (workspace_id)"))
        if "ix_agent_memory_profiles_user_id" not in memory_indexes:
            with engine.begin() as connection:
                connection.execute(text("CREATE INDEX ix_agent_memory_profiles_user_id ON agent_memory_profiles (user_id)"))
        if "ix_agent_memory_profiles_agent_id" not in memory_indexes:
            with engine.begin() as connection:
                connection.execute(text("CREATE INDEX ix_agent_memory_profiles_agent_id ON agent_memory_profiles (agent_id)"))
        unique_constraints = {constraint["name"] for constraint in inspector.get_unique_constraints("agent_memory_profiles")}
        if "uq_agent_memory_profile_scope" not in unique_constraints and "uq_agent_memory_profile_scope" not in memory_indexes:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_memory_profile_scope "
                        "ON agent_memory_profiles (workspace_id, user_id, agent_id)"
                    )
                )
    if "prompt_templates" in table_names:
        _ensure_columns(
            "prompt_templates",
            {
                "workspace_id": "INTEGER",
                "user_id": "INTEGER",
                "title": "VARCHAR(160)",
                "description": "TEXT DEFAULT ''",
                "content": "TEXT DEFAULT ''",
                "category": "VARCHAR(80) DEFAULT 'general'",
                "tags": "JSON DEFAULT '[]'",
                "enabled": "BOOLEAN DEFAULT true",
                "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
                "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            },
        )
        prompt_indexes = {index["name"] for index in inspector.get_indexes("prompt_templates")}
        if "ix_prompt_templates_workspace_id" not in prompt_indexes:
            with engine.begin() as connection:
                connection.execute(text("CREATE INDEX ix_prompt_templates_workspace_id ON prompt_templates (workspace_id)"))
        if "ix_prompt_templates_user_id" not in prompt_indexes:
            with engine.begin() as connection:
                connection.execute(text("CREATE INDEX ix_prompt_templates_user_id ON prompt_templates (user_id)"))
        if "ix_prompt_templates_category" not in prompt_indexes:
            with engine.begin() as connection:
                connection.execute(text("CREATE INDEX ix_prompt_templates_category ON prompt_templates (category)"))
        if "ix_prompt_templates_enabled" not in prompt_indexes:
            with engine.begin() as connection:
                connection.execute(text("CREATE INDEX ix_prompt_templates_enabled ON prompt_templates (enabled)"))
        unique_constraints = {constraint["name"] for constraint in inspector.get_unique_constraints("prompt_templates")}
        if "uq_prompt_templates_owner_title" not in unique_constraints and "uq_prompt_templates_owner_title" not in prompt_indexes:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_prompt_templates_owner_title "
                        "ON prompt_templates (workspace_id, user_id, title)"
                    )
                )
    if "knowledge_documents" in table_names:
        if engine.dialect.name == "postgresql":
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE knowledge_documents ALTER COLUMN content_type TYPE VARCHAR(120)"))
        elif engine.dialect.name == "mysql":
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE knowledge_documents MODIFY COLUMN content_type VARCHAR(120)"))
        _ensure_columns(
            "knowledge_documents",
            {
                "title": "VARCHAR(255) DEFAULT ''",
                "source_type": "VARCHAR(20) DEFAULT 'text'",
                "text_preview": "TEXT DEFAULT ''",
                "chunk_count": "INTEGER DEFAULT 0",
                "error_message": "TEXT DEFAULT ''",
                "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
                "segment_config": "JSON",
            },
        )

        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE knowledge_documents "
                    "SET title = filename "
                    "WHERE title IS NULL OR title = ''"
                )
            )
            connection.execute(
                text(
                    "UPDATE knowledge_documents "
                    "SET text_preview = substr(text, 1, 180) "
                    "WHERE text_preview IS NULL OR text_preview = ''"
                )
            )
            connection.execute(
                text(
                    "UPDATE knowledge_documents "
                    "SET chunk_count = ("
                    "SELECT count(*) FROM knowledge_chunks "
                    "WHERE knowledge_chunks.document_id = knowledge_documents.id"
                    ") "
                    "WHERE chunk_count IS NULL OR chunk_count = 0"
                )
            )
    if "knowledge_chunks" in table_names:
        _ensure_columns(
            "knowledge_chunks",
            {
                "parent_id": "VARCHAR(120) DEFAULT ''",
                "chunk_id": "VARCHAR(120) DEFAULT ''",
                "title": "VARCHAR(255) DEFAULT ''",
                "page": "INTEGER",
                "section": "VARCHAR(255) DEFAULT ''",
                "content_hash": "VARCHAR(80) DEFAULT ''",
                "embedding_model": "VARCHAR(160) DEFAULT ''",
                "embedding_dimension": "INTEGER DEFAULT 0",
                "metadata": "JSON DEFAULT '{}'",
            },
        )
        chunk_indexes = {index["name"] for index in inspector.get_indexes("knowledge_chunks")}
        for index_name, column_name in {
            "ix_knowledge_chunks_parent_id": "parent_id",
            "ix_knowledge_chunks_chunk_id": "chunk_id",
            "ix_knowledge_chunks_content_hash": "content_hash",
        }.items():
            if index_name not in chunk_indexes:
                with engine.begin() as connection:
                    connection.execute(text(f"CREATE INDEX {index_name} ON knowledge_chunks ({column_name})"))
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE knowledge_chunks SET chunk_id = vector_id "
                    "WHERE chunk_id IS NULL OR chunk_id = ''"
                )
            )
            connection.execute(
                text(
                    "UPDATE knowledge_chunks SET parent_id = vector_id "
                    "WHERE parent_id IS NULL OR parent_id = ''"
                )
            )
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workspaces SET name = replace(name, ' 的团队', ' 的工作台') "
                "WHERE name LIKE '% 的团队'"
            )
        )
        connection.execute(
            text(
                "UPDATE agents SET description = :new_value "
                "WHERE description = :old_value"
            ),
            {
                "old_value": "面向团队内部使用的自定义智能体。",
                "new_value": "用于个人或项目场景的自定义智能体。",
            },
        )
        connection.execute(
            text(
                "UPDATE agents SET opening_message = :new_value "
                "WHERE opening_message = :old_value"
            ),
            {
                "old_value": "你好，我是你的团队智能体。",
                "new_value": "你好，我是你的智能体。",
            },
        )
        connection.execute(
            text(
                "UPDATE agents SET system_prompt = :new_value "
                "WHERE system_prompt = :old_value"
            ),
            {
                "old_value": "你是一个谨慎、清晰的团队智能体。优先使用绑定知识库和工具输出回答。",
                "new_value": "你是一个谨慎、清晰的智能体。优先使用绑定知识库和工具输出回答。",
            },
        )

    if "uploads" in table_names:
        _ensure_columns(
            "uploads",
            {
                "storage_key": "VARCHAR(255) DEFAULT ''",
            },
        )
        if engine.dialect.name == "mysql":
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE uploads MODIFY COLUMN data_url MEDIUMTEXT NOT NULL"))
                connection.execute(text("ALTER TABLE uploads MODIFY COLUMN text MEDIUMTEXT NOT NULL"))

    # 🛡️ MySQL 的 TEXT 列上限仅 64KB，学术 PDF 正文/长会话会超（迁移自 PG 无限 TEXT 时埋的坑，
    # 表现为大文档上传 500「Data too long for column 'text'」）。把承载大正文的列加宽为 LONGTEXT。
    # 幂等：已是 LONGTEXT 则跳过，避免每次启动重写大表。
    if engine.dialect.name == "mysql":
        longtext_targets = [
            ("knowledge_documents", "text"),
            ("knowledge_chunks", "text"),
            ("knowledge_parent_chunks", "text"),
            ("messages", "content"),
        ]
        for tbl, col in longtext_targets:
            if tbl not in table_names:
                continue
            col_info = next((c for c in inspector.get_columns(tbl) if c["name"] == col), None)
            if col_info and "LONGTEXT" not in str(col_info["type"]).upper():
                with engine.begin() as connection:
                    connection.execute(text(f"ALTER TABLE {tbl} MODIFY COLUMN {col} LONGTEXT NOT NULL"))

    if "sessions" in table_names:
        _ensure_columns(
            "sessions",
            {
                "is_debug": "BOOLEAN DEFAULT false",
            },
        )
    if "session_memory" in table_names:
        _ensure_columns(
            "session_memory",
            {
                "version": "INTEGER DEFAULT 0",
            },
        )


def get_db():
    """
    FastAPI 依赖注入专用的数据库 Session 生成器。

    🎯 意图与工程大局观：
        本函数被设计为依赖注入函数（Dependency Injection Component），在 API 端点执行前
        自动从连接池拉取连接，并开启逻辑会话事务。

    🛡️ 防御性编程：
        采用 try-finally 控制块，无论 API 端点在处理业务逻辑时是否抛出未捕获的错误，
        最终一定会显式执行 `db.close()`，回收物理连接至连接池，杜绝高并发环境下的“连接池枯竭”故障。
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 🛡️ DDL 标识符白名单正则：仅允许标准 SQL 标识符字符
_VALID_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")
# 🛡️ DDL 类型白名单：仅允许已知安全的 SQL 类型
_VALID_DDL_TYPES = {
    "BOOLEAN", "INTEGER", "BIGINT", "FLOAT", "DOUBLE",
    "VARCHAR", "TEXT", "JSON", "TIMESTAMP",
}


def _ensure_columns(table_name: str, columns: dict[str, str]) -> None:
    """
    内部辅助方法：确保目标表存在指定的列，如缺失则自动通过 ALTER TABLE 注入。

    🛡️ 防御性设计：
        - 通过正则白名单校验 table_name 和 column_name，防范 SQL 注入
        - 通过 DDL 类型白名单校验类型声明
        - 添加前读取已有表结构缓存，避免重复 DDL 产生 Duplicate Column 异常
    """
    if not _VALID_IDENTIFIER.match(table_name):
        raise ValueError(f"Invalid table name for DDL: {table_name}")
    existing = {column["name"] for column in inspect(engine).get_columns(table_name)}
    for column_name, ddl in columns.items():
        if column_name in existing:
            continue
        if not _VALID_IDENTIFIER.match(column_name):
            raise ValueError(f"Invalid column name for DDL: {column_name}")
        # 抽取 DDL 中的类型关键字做白名单校验
        ddl_type = ddl.split()[0].split("(", 1)[0].upper() if ddl else ""
        if ddl_type not in _VALID_DDL_TYPES:
            raise ValueError(f"Unsupported DDL type for column {column_name}: {ddl_type}")
        
        # MySQL doesn't support DEFAULT value for JSON, TEXT, BLOB columns during ALTER TABLE
        if engine.dialect.name == "mysql" and ddl_type in ("JSON", "TEXT"):
            ddl = re.sub(r"\bDEFAULT\s+('[^']*'|[^ ]+)", "", ddl, flags=re.IGNORECASE).strip()

        with engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"))

