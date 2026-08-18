"""
日志记录器
"""

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



