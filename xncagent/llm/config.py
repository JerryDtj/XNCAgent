import os
from pathlib import Path
from dotenv import load_dotenv

from xncagent.config import Config
from xncagent.utils.exceptions import NotFoundException
from xncagent.utils.logger import logger


BASE_DIR = Path(__file__).resolve().parent
# 项目根目录的 .env 优先；llm/.env 仅用于本地开发覆盖
_PROJECT_DIR = BASE_DIR.parent.parent
load_dotenv(_PROJECT_DIR / ".env")
load_dotenv(BASE_DIR / ".env", override=True)

class Settings:
    agictor_base_url: str = os.getenv("AGICTOR_BASE_URL","https://api.agicto.cn/v1")
    agictor_api_key: str = os.getenv("AGICTOR_API_KEY")
    check_model_name: str = Config['llm'].get('check_model_name',"deepseek-v4-flash")
    task_model_name: str = Config['llm'].get('task_model_name',"Qwen/Qwen2.5-7B-Instruct")

    def require_api_key(self) -> str:
        if not self.agictor_api_key:
            raise NotFoundException("AGICTOR_API_KEY没有找到");
        return self.agictor_api_key

settings = Settings()

AGICTOR_BASE_URL = settings.agictor_base_url
AGICTOR_API_KEY = settings.agictor_api_key
CHECK_MODEL_NAME = settings.check_model_name
TASK_MODEL_NAME = settings.task_model_name

logger.info(
    "[LLM] 生效配置 "
    f"base_url={AGICTOR_BASE_URL} "
    f"check_model_name={CHECK_MODEL_NAME} "
    f"task_model_name={TASK_MODEL_NAME}"
)