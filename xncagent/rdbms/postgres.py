from psycopg.conninfo import make_conninfo
from psycopg_pool import Pool
from psycopg.rows import dict_row
from contextlib import contextmanager
from psycopg.connection import ConnectionPool

from xncagent.config import config
from xncagent.utils.logger import logger

_pool: ConnectionPool | None = None

def _make_conninfo(db_cfg) -> str:
    params = {
        "host": db_cfg.host,
        "port": db_cfg.port,
        "dbname": db_cfg.dbname,
        "user": db_cfg.user,
        "password": db_cfg.password,
        "sslmode": db_cfg.sslmode,
        "connect_timeout": db_cfg.connect_timeout,
        "application_name": db_cfg.application_name,
    }
    if getattr(db_cfg, "options", None):
        params["options"] = db_cfg.options
    return make_conninfo(**params)

def init_pool(db_cfg=None, pool_cfg=None) -> None:
    """显式初始化连接池。测试里可传自定义配置。"""
    global _pool
    if _pool is not None:
        return
    db_cfg = db_cfg or config.database
    pool_cfg = pool_cfg or config.pool
    _pool = ConnectionPool(
        conninfo=_make_conninfo(db_cfg),
        min_size=pool_cfg.min_size,
        max_size=pool_cfg.max_size,
        timeout=pool_cfg.timeout,
        kwargs={"row_factory": dict_row},
        open=False,
    )
    _pool.open()
    logger.info("连接池已启动: %s", db_cfg.application_name)

def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
        logger.info("连接池已关闭")

@contextmanager
def get_cursor():
    if _pool is None:
        raise RuntimeError("连接池未初始化，请先调用 init_pool()")
    with _pool.connection() as conn:
        with conn.cursor() as cur:
            yield cur
