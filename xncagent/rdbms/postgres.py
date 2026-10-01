from contextlib import contextmanager
import threading

from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from xncagent.rdbms.config import config
from xncagent.utils.logger import logger

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()

def _make_conninfo(db_cfg) -> str:
    # 会话时区固定东八区，TIMESTAMPTZ 读出来就是本地钟面。
    options = "-c timezone=Asia/Shanghai"
    extra = getattr(db_cfg, "options", None)
    if extra:
        options = f"{options} {extra}"
    params = {
        "host": db_cfg.host,
        "port": db_cfg.port,
        "dbname": db_cfg.dbname,
        "user": db_cfg.user,
        "password": db_cfg.password,
        "sslmode": db_cfg.sslmode,
        "connect_timeout": db_cfg.connect_timeout,
        "application_name": db_cfg.application_name,
        "options": options,
    }
    return make_conninfo(**params)

def init_pool(db_cfg=None, pool_cfg=None) -> None:
    """每个进程建一次。同进程内的线程共用这一个池。"""
    global _pool
    if _pool is not None:
        return
    with _pool_lock:
        if _pool is not None:
            return
        db_cfg = db_cfg or config.database
        pool_cfg = pool_cfg or config.pool
        pool = ConnectionPool(
            conninfo=_make_conninfo(db_cfg),
            min_size=pool_cfg.min_size,
            max_size=pool_cfg.max_size,
            timeout=pool_cfg.timeout,
            kwargs={"row_factory": dict_row},
            open=False,
        )
        pool.open()
        _pool = pool
        logger.info("连接池已启动: {}", db_cfg.application_name)

def close_pool() -> None:
    global _pool
    with _pool_lock:
        pool = _pool
        _pool = None
    if pool is not None:
        pool.close()
        logger.info("连接池已关闭")

@contextmanager
def get_cursor():
    if _pool is None:
        raise RuntimeError("连接池未初始化，请先调用 init_pool()")
    with _pool.connection() as conn:
        with conn.cursor() as cur:
            yield cur
