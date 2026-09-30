import os
import yaml
from dotenv import load_dotenv
from pathlib import Path
from pydantic import BaseModel, config


from xncagent.utils.exceptions import NotFoundException


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "db_config.yaml"

class DatabaseConfig(BaseModel):
    host: str = "localhost"
    port: int = 5432
    dbname: str = "xncagent"
    user: str = "xnc"
    password: str
    sslmode: str = "disable"
    connect_timeout: int = 30
    application_name: str = "xncagent"
    options: str | None = None

class PoolConfig(BaseModel):
    min_size: int = 1
    max_size: int = 10
    timeout: int = 30

class AppConfig(BaseModel):
    database: DatabaseConfig
    pool: PoolConfig

def _replace_env_vars(obj):
    """递归替换 ${VAR} 和 ${VAR:-default}"""
    if isinstance(obj, dict):
        return {k: _replace_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_env_vars(v) for v in obj]
    if isinstance(obj, str):
        if obj.startswith("${") and obj.endswith("}"):
            inner = obj[2:-1]
            if ":-" in inner:
                var, default = inner.split(":-", 1)
                return os.getenv(var, default)
            value = os.getenv(inner)
            if value is None:
                raise ValueError(f"环境变量 {inner} 未设置")
            return value
        return os.path.expandvars(obj)
    return obj

def load_config(path: str | Path | None = None) -> AppConfig:
    if path is None:
        path = CONFIG_PATH
    path = Path(path)
    if not path.exists:
        raise NotFoundException("未找到config目录")
    
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    raw = _replace_env_vars(raw)
    return AppConfig(**raw)

config = load_config(CONFIG_PATH)

    