import os
from pathlib import Path
from dotenv import load_dotenv

from xncagent.utils.exceptions import NotFoundException


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

class Settings:
    agictor_base_url: str = os.getenv("AGICTOR_BASE_URL","https://api.agicto.cn/v1")
    agictor_api_key: str = os.getenv("AGICTOR_API_KEY")
    model_name: str = os.getenv("MODEL_NAME","deepseek-v4-flash")
    
    def require_api_key(self) -> str:
        if not self.agictor_api_key:
            raise NotFoundException("AGICTOR_API_KEY没有找到");
        return self.agictor_api_key

settings = Settings()

AGICTOR_BASE_URL = settings.agictor_base_url
AGICTOR_API_KEY = settings.agictor_api_key
MODEL_NAME = settings.model_name