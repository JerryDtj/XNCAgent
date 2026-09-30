"""
日志记录器
"""

import logging
from pathlib import Path
import sys
from xncagent.config import Config
from loguru import logger


log_config = Config.get('logger', {})
LOG_LEVEL = log_config.get('level', "INFO")
LOG_FILENAME = log_config.get('filename', "logs/xncagent.log")
LOG_MAX_SIZE = log_config.get('max_size', "10 MB")
LOG_KEEP_DAYS = log_config.get('keep_days', 30)
CONSOLE_FORMAT = log_config.get('console_format', "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>")
FILE_FORMAT = log_config.get('file_formt', "{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} - {message}")

# 创建日志目录
log_path = Path(LOG_FILENAME)
log_path.parent.mkdir(parents=True, exist_ok=True)

# 移除所有默认的处理器
logger.remove()

#添加控制台和文件日志
logger.add(sys.stderr, level=LOG_LEVEL, format=CONSOLE_FORMAT)
logger.add(
    LOG_FILENAME, 
    level=LOG_LEVEL, 
    rotation=LOG_MAX_SIZE, 
    retention=LOG_KEEP_DAYS, 
    format=FILE_FORMAT,
    encoding="utf-8",
)


# uvicorn access log 走标准库 logging，与上面的 loguru 分开配置。
_NOISY_ACCESS_PATHS = {"/json/version", "/favicon.ico"}


class _NoisyAccessPathFilter(logging.Filter):
    """丢弃浏览器固定探测路径的 access 记录，其余 404 保留。"""

    def filter(self, record: logging.LogRecord) -> bool:
        path = _access_log_path(record)
        if path is None:
            return True
        return path.split("?", 1)[0] not in _NOISY_ACCESS_PATHS


def _access_log_path(record: logging.LogRecord) -> str | None:
    args = record.args
    if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
        return args[2]
    return None


def suppress_noisy_access_logs() -> None:
    """把探测路径过滤器挂到 uvicorn.access，重复调用只挂一次。"""
    access_logger = logging.getLogger("uvicorn.access")
    if any(isinstance(existing, _NoisyAccessPathFilter) for existing in access_logger.filters):
        return
    access_logger.addFilter(_NoisyAccessPathFilter())



