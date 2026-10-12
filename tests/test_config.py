from core.config import Settings

# 🛡️ 测试封闭性：CI/本地 shell 可能全局导出这些变量（如 CI 的 LINGSHU_MOCK_LLM=true、
#    JWT_SECRET=test-...），若漏进 Settings 会污染生产就绪检查的断言，先统一清空。
_AMBIENT_ENV_KEYS = [
    "LINGSHU_DEPLOYMENT_MODE",
    "JWT_SECRET",
    "API_KEY_ENCRYPTION_KEY",
    "LINGSHU_MOCK_LLM",
    "SWEEPER_MOCK_LLM",
    "LINGSHU_VECTOR_BACKEND",
    "SWEEPER_VECTOR_BACKEND",
    "DATABASE_URL",
    "REDIS_URL",
    "CELERY_ENABLED",
    "CORS_ORIGINS",
    "USER_MODEL_ALLOW_PRIVATE_NETWORK",
    "STORAGE_BACKEND",
    "STORAGE_ACCESS_KEY",
    "STORAGE_SECRET_KEY",
]


def _settings(monkeypatch, **env):
    for key in _AMBIENT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_production_readiness_is_not_applied_in_development(monkeypatch):
    settings = _settings(monkeypatch, LINGSHU_DEPLOYMENT_MODE="development")

    assert settings.production_readiness_issues() == []


def test_production_readiness_blocks_development_defaults(monkeypatch):
    settings = _settings(monkeypatch, LINGSHU_DEPLOYMENT_MODE="production")

    issues = settings.production_readiness_issues()

    assert any("JWT_SECRET" in issue for issue in issues)
    assert any("API_KEY_ENCRYPTION_KEY" in issue for issue in issues)
    assert any("LINGSHU_VECTOR_BACKEND=memory" in issue for issue in issues)
    assert any("CELERY_ENABLED=true" in issue for issue in issues)


def test_production_readiness_accepts_hardened_config(monkeypatch):
    settings = _settings(
        monkeypatch,
        LINGSHU_DEPLOYMENT_MODE="production",
        JWT_SECRET="x" * 48,
        API_KEY_ENCRYPTION_KEY="y" * 48,
        DATABASE_URL="mysql+pymysql://lingshu_prod:strong-password@db:3306/lingshu_agent",
        REDIS_URL="redis://redis:6379/0",
        CELERY_ENABLED="true",
        LINGSHU_VECTOR_BACKEND="milvus",
        CORS_ORIGINS="https://agent.example.com",
        STORAGE_BACKEND="minio",
        STORAGE_ACCESS_KEY="lingshu-prod",
        STORAGE_SECRET_KEY="z" * 48,
    )

    assert settings.production_readiness_issues() == []


def test_production_readiness_blocks_runtime_vector_fallback(monkeypatch):
    """运行期降级必须在生产就绪检查里被拦下。

    🛡️ 守卫 `_vector_store.settings is self` 只在向量库单例确由「本 Settings 实例」驱动时才检查其状态，
    本例正是该情形：把单例的 settings 指向本实例，模拟「生产配置 milvus 但连不上、已回落到内存」。
    若把该守卫写成恒假，这条用例会失败 —— 保证拦截能力没有被守卫顺手改没。
    """
    from core.integrations import vector_store as vs_module

    settings = _settings(
        monkeypatch,
        LINGSHU_DEPLOYMENT_MODE="production",
        JWT_SECRET="x" * 48,
        API_KEY_ENCRYPTION_KEY="y" * 48,
        DATABASE_URL="mysql+pymysql://lingshu_prod:strong-password@db:3306/lingshu_agent",
        REDIS_URL="redis://redis:6379/0",
        CELERY_ENABLED="true",
        LINGSHU_VECTOR_BACKEND="milvus",
        CORS_ORIGINS="https://agent.example.com",
        STORAGE_BACKEND="minio",
        STORAGE_ACCESS_KEY="lingshu-prod",
        STORAGE_SECRET_KEY="z" * 48,
    )
    store = vs_module.vector_store
    # 🛡️ 全部走 monkeypatch，用例结束自动还原，避免污染后续用例。
    monkeypatch.setattr(store, "settings", settings)
    monkeypatch.setattr(store, "_client", None)
    monkeypatch.setattr(store, "_error", "connection refused")

    issues = settings.production_readiness_issues()

    fallback_issues = [issue for issue in issues if "Milvus connection is unavailable" in issue]
    assert len(fallback_issues) == 1
    assert "connection refused" in fallback_issues[0]


def test_vector_backend_is_normalized(monkeypatch):
    """向量后端名必须去空白 + 转小写。

    🛡️ 若不在配置层收口，`Milvus` / `" milvus "` 这类写法会让向量库单例**根本不尝试连接**
    （`vector_store.py` 按字面比较 `== "milvus"`），于是 `_client is None` 却不算降级，
    向量被静默写进进程内内存 —— 既绕过「降级拒写」也绕过「生产禁降级」。
    """
    assert _settings(monkeypatch, LINGSHU_VECTOR_BACKEND="Milvus").vector_backend == "milvus"
    assert _settings(monkeypatch, LINGSHU_VECTOR_BACKEND="  MILVUS  ").vector_backend == "milvus"


def test_vector_backend_normalization_does_not_whitelist(monkeypatch):
    """只做 strip+lower，不校验取值集合：未知后端应原样通过而不报错。"""
    assert _settings(monkeypatch, LINGSHU_VECTOR_BACKEND="qdrant").vector_backend == "qdrant"
