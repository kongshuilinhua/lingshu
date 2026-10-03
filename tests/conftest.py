import importlib
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url


@pytest.fixture(scope="session")
def _test_db_url():
    """session 级测试库:优先 TEST_DATABASE_URL 环境变量,无则起 testcontainers MySQL。

    让本地无测试库也能跑 client-fixture 集成测试(消除 ~88 skip)。
    container session 级复用,schema 由 client fixture function 级重建。
    """
    env = os.getenv("TEST_DATABASE_URL")
    if env:
        yield env
        return
    from testcontainers.community.mysql import MySqlContainer
    import pymysql

    container = MySqlContainer("mysql:8.0", dialect="pymysql", root_password="rootpass")
    container.start()
    try:
        # CI 的 mysql:8.0 service 设了 log_bin_trust_function_creators=1 让 init_db 创建 trigger;
        # testcontainers 容器默认未设 → 用 root 连接设 GLOBAL,否则 CREATE TRIGGER 报 errno 1419。
        conn = pymysql.connect(
            host=container.get_container_host_ip(),
            port=int(container.get_exposed_port(3306)),
            user="root",
            password="rootpass",
        )
        with conn.cursor() as cur:
            cur.execute("SET GLOBAL log_bin_trust_function_creators = 1")
        conn.close()
        yield container.get_connection_url()
    finally:
        container.stop()


@pytest.fixture()
def client(monkeypatch, _test_db_url):
    database_url = _test_db_url

    _terminate_database_connections(database_url)
    
    # For MySQL, terminate other active connections to prevent metadata lock hangs during DROP TABLE
    url = make_url(database_url)
    if url.get_dialect().name == "mysql":
        try:
            temp_engine = create_engine(database_url, future=True)
            with temp_engine.connect() as conn:
                cur_id = conn.execute(text("SELECT CONNECTION_ID()")).scalar()
                processes = conn.execute(text("SHOW PROCESSLIST")).fetchall()
                for p in processes:
                    p_id = p[0]
                    if p_id != cur_id:
                        try:
                            conn.execute(text(f"KILL {p_id}"))
                        except Exception:
                            pass
            temp_engine.dispose()
        except Exception:
            pass

    engine = create_engine(database_url, future=True)
    try:
        with engine.begin() as connection:
            dialect_name = engine.dialect.name
            if dialect_name == "postgresql":
                connection.execute(text("SET lock_timeout = '10s'"))
                connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
                connection.execute(text("CREATE SCHEMA public"))
            elif dialect_name == "mysql":
                connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
                db_inspector = inspect(engine)
                for table in db_inspector.get_table_names():
                    connection.execute(text(f"DROP TABLE IF EXISTS `{table}`"))
                connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    except Exception as exc:
        pytest.skip(f"Test database is not available: {exc}")
    finally:
        engine.dispose()

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("LINGSHU_MOCK_LLM", "true")
    monkeypatch.setenv("LINGSHU_VECTOR_BACKEND", "memory")
    # 测试隔离：强制关闭 Redis / Celery，避免本机 .env 里的 REDIS_URL 让真实限流跨用例累计
    # 把注册/登录打成 429、或缓存/熔断状态跨用例串味（Redis 不像 DB 每个用例重建）。
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("CELERY_ENABLED", "false")
    # 上传体积限额测试假设 8MB；运行时默认已调大到 30MB，固定测试环境为 8MB 让限额机制校验成立。
    monkeypatch.setenv("UPLOAD_MAX_BYTES", str(8 * 1024 * 1024))
    for key in [
        "OPENAI_API_KEY",
        "DASHSCOPE_API_KEY",
        "DEEPSEEK_API_KEY",
        "EMBEDDING_API_KEY",
        "RERANK_API_KEY",
    ]:
        monkeypatch.setenv(key, "")
    import core.config

    core.config.get_settings.cache_clear()
    # redis_store 是模块级单例，进程启动时已按 .env 连上真实 Redis；上面的 setenv 改不动它。
    # 直接把已建客户端置空：其所有方法经 _redis_guard 走降级（限流放行、缓存未命中、不熔断），
    # 让全部用例对 Redis 真正无依赖。所有模块持有的是同一个对象引用，故就地置空即全局生效。
    import core.services.rag_cache as rag_cache_module

    rag_cache_module.redis_store._client = None
    rag_cache_module.redis_store._error = ""
    rag_cache_module.redis_store._runtime_error = ""
    import core.db.session as db_session
    import core.integrations.vector_store as vector_module
    import core.services.knowledge as knowledge_service
    import core.runtime.workflow as workflow_runtime
    import api.main as main

    import api.routes.knowledge as knowledge_route
    import api.routes.chat as chat_route
    importlib.reload(db_session)
    importlib.reload(vector_module)
    importlib.reload(knowledge_service)
    importlib.reload(workflow_runtime)
    importlib.reload(knowledge_route)
    importlib.reload(chat_route)
    importlib.reload(main)
    db_session.init_db()
    with TestClient(main.app) as test_client:
        try:
            yield test_client
        finally:
            db_session.engine.dispose()


def _terminate_database_connections(database_url: str) -> None:
    """Terminate active connections to the test database (PostgreSQL only)."""
    url = make_url(database_url)
    if url.get_dialect().name != "postgresql":
        return  # Only applicable to PostgreSQL
    database = url.database
    if not database:
        return
    admin_url = url.set(database="postgres")
    admin_engine = create_engine(admin_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.begin() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) "
                    "FROM pg_stat_activity "
                    "WHERE datname = :database AND pid <> pg_backend_pid()"
                ),
                {"database": database},
            )
    finally:
        admin_engine.dispose()


@pytest.fixture()
def owner_token(client):
    response = client.post(
        "/api/auth/register",
        json={"email": "owner@example.com", "name": "Owner", "password": "password123"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


@pytest.fixture()
def auth_headers(owner_token):
    return {"Authorization": f"Bearer {owner_token}"}


@pytest.fixture(autouse=True)
def run_background_tasks_synchronously(monkeypatch):
    from fastapi import BackgroundTasks
    def mock_add_task(self, func, *args, **kwargs):
        func(*args, **kwargs)
    monkeypatch.setattr(BackgroundTasks, "add_task", mock_add_task)

