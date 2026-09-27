import yaml
from pathlib import Path
from functools import lru_cache

_PROMPTS_DIR = Path(__file__).resolve().parent

@lru_cache(maxsize=None)
def load_system_prompt(name: str) -> str:
    """
    加载提示词
    :param name: 提示词名称
    :return: 提示词
    """
    path = _PROMPTS_DIR / f"{name}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data["system_prompt"].strip()