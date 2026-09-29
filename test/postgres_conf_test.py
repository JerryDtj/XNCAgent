# tests/conftest.py
import os
import uuid
import pytest
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

# ===== 必须在 import src 之前覆盖环境变量 =====
os.environ["DB_HOST"] = os.environ.get("TEST_DB_HOST", "localhost")
os.environ["DB_PORT"] = os.environ.get("TEST_DB_PORT", "5432")
os.environ["DB_NAME"] = os.environ.get("TEST_DB_NAME", "myapp_test")
os.environ["DB_USER"] = os.environ.get("TEST_DB_USER", "myapp_test_user")
os.environ["DB_PASSWORD"] = os.environ.get("TEST_DB_PASSWORD", "test_password")

from xncagent.rdbms.config import DatabaseConfig, PoolConfig  # noqa: E402
from xncagent.rdbms import postgres as db_module                    # noqa: E402


def _worker_id() -> str:
    return os.environ.get("PYTEST_XDIST_WORKER", "master")


def _admin_conninfo() -> str:
    return make_conninfo(
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


@pytest.fixture(scope="session")
def test_schema() -> str:
    return f"test_{_worker_id()}_{uuid.uuid4().hex[:6]}"


@pytest.fixture(scope="session", autouse=True)
def pg_pool(test_schema):
    # 1. 建独立 schema
    with psycopg.connect(_admin_conninfo(), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                    sql.Identifier(test_schema)
                )
            )

    # 2. 用 search_path 指向测试 schema 初始化连接池
    db_cfg = DatabaseConfig(
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        application_name=f"pytest_{_worker_id()}",
        options=f"-c search_path={test_schema},public",
    )
    pool_cfg = PoolConfig(min_size=1, max_size=4)
    db_module.init_pool(db_cfg=db_cfg, pool_cfg=pool_cfg)

    # 3. 建表（直接写 SQL，不依赖 crud.py）
    with db_module.get_cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                email VARCHAR(100),
                age INTEGER
            )
        """)

    yield

    # 4. 清理
    db_module.close_pool()
    with psycopg.connect(_admin_conninfo(), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(test_schema)
                )
            )


@pytest.fixture(autouse=True)
def clean_users(pg_pool):
    """每个测试后清空表，测试之间互不污染"""
    yield
    with db_module.get_cursor() as cur:
        cur.execute("TRUNCATE TABLE users RESTART IDENTITY CASCADE")