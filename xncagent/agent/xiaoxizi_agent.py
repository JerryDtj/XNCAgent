from pathlib import Path
from typing import Optional, Set
from xncagent.llm.llm_task import get_history_by_user_id
from xncagent.utils.logger import logger
from xncagent.utils.context import get_user_id
from xncagent.llm.query_rewrite import rewrite_query
import yaml


YAML_PATH = Path("xncagent/config/precheck_words.yaml")
MIN_LENGTH = 10

def load_precheck_words(path: Path) -> Set[str]:
    with open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    MIN_LENGTH = data["categories"]["min_length"]

    keyword: Set[str] = set()
    for _,words in data["categories"].items():
        if words:
            keyword.update(w.strip() for w in words if isinstance(w, str) and w.strip())
    return keyword

KEYWORDS: Set[str] = load_precheck_words(YAML_PATH)
logger.info(f"已加载关键词 {len(KEYWORDS)} 个")

def need_rewrite(query: str) -> bool:
    if len(query) < MIN_LENGTH :
        return True
    return any(keyword in query for keyword in KEYWORDS)

def task_check(query: str) -> Optional[dict]:
    """
    检查用户输入是否需要改写，并返回改写后的查询。

    :param query: 用户原始输入
    :return: {
        "rewritten_query": str,   # 改写后的完整问题；自包含时原样返回
        "confidence": float,      # 模型自报置信度，只记日志不参与决策
    }
    调用失败抛异常，由调用方兜底（返回兜底话术 + 记日志）。
    """
    if need_rewrite(query):
        user_id = get_user_id()
        if user_id is not None:
            # 这里先写个空函数，回头在补齐这个函数
            history = get_history_by_user_id(user_id)
            result = rewrite_query(query, history)
            if result is not None:
                # 调用rag查询
                return None
    else:
        # 调用hf模型获取对话场景
        return None
    return None



    
    
            
            


