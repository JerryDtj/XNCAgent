import os
from pathlib import Path
from dotenv import load_dotenv

from xncagent.utils.exceptions import NotFoundException


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BASE_URL = os.getenv("AGICTOR_BASE_URL","https://api.agicto.cn/v1")
API_KEY = os.getenv("AGICTOR_API_KEY")
if not API_KEY:
    raise NotFoundException(message="api key 未配置")