from pathlib import Path
from typing import Optional, Set

import yaml

from xncagent.llm.llm_task import get_history_by_user_id
from xncagent.llm.query_rewrite import query_rewrite, SCENE_WHITELIST
from xncagent.scene_matcher import match_scene
from xncagent.utils.logger import logger
from xncagent.utils.context import get_user_id
from xncagent.schemas.query_rewrite import RewriterQuestionResponse
from xncagent.rag.rag_retriever import retrieve_knowledge



YAML_PATH = Path(__file__).resolve().parent.parent / "config" / "precheck_words.yaml"
MIN_LENGTH = 10


def load_precheck_words(path: Path) -> Set[str]:
    global MIN_LENGTH
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    MIN_LENGTH = data["categories"]["min_length"]

    keyword: Set[str] = set()
    for category, words in data["categories"].items():
        if category == "min_length" or not words:
            continue
        keyword.update(w.strip() for w in words if isinstance(w, str) and w.strip())
    return keyword


KEYWORDS: Set[str] = load_precheck_words(YAML_PATH)
logger.info(f"已加载关键词 {len(KEYWORDS)} 个")


def need_rewrite(query: str) -> bool:
    if len(query) < MIN_LENGTH:
        return True
    return any(keyword in query for keyword in KEYWORDS)


def _understand(query: str, history: str) -> RewriterQuestionResponse:
    """LLM 统一调用 + 本地交叉校验，返回标准化的理解结果 dict。"""
    rewrite = query_rewrite(query, history)
    llm_scene = rewrite.scene

    # 交叉校验（角色 B）：全量记录 matcher_scene/matcher_score（阈值校准
    # 需要一致案例的分数分布全貌），不一致时额外记 WARN，只攒数据不参与决策。
    matched_scene, score = match_scene(query)
    logger.info(
        f"场景交叉校验: query={query!r} llm={llm_scene} "
        f"matcher={matched_scene}({score:.3f})"
    )
    if matched_scene != llm_scene:
        logger.warning(
            f"场景交叉校验不一致: query={query!r} "
            f"llm={llm_scene} matcher={matched_scene}({score:.3f})"
        )

    return rewrite


def task(query: str) -> Optional[dict]:
    """
    检查用户输入，返回理解结果。

    :param query: 用户原始输入
    :return: {
        "rewritten_query": str,
        "scene": str,        # 6 值之一：5 个场景白名单 + "无关闲聊"
        "emotion_alert": bool,
        "confidence": float,
    }
    主路径：预检决定带不带历史，LLM 统一调用（改写 + 场景 + 情绪预警）。
    降级通道（角色 A）：LLM 调用失败时，用本地 bge 匹配给出 scene 继续走 RAG。
    """
    history = ""
    if need_rewrite(query):
        user_id = get_user_id()
        if user_id is not None:
            history = get_history_by_user_id(user_id)

    try:
        check_result: RewriterQuestionResponse = _understand(query, history)
        knowledge_result: str = retrieve_knowledge(check_result.rewritten_query,check_result.scene)
        
        
    except Exception as e:
        # 降级通道：LLM 不可用，本地场景匹配兜底，保证链路可用
        logger.error(f"LLM 统一调用失败，降级为本地场景匹配: {e}")
        scene, score = match_scene(query)
        return {
            "rewritten_query": query,
            "scene": scene,
            "emotion_alert": False,
            "confidence": score,
            "degraded": True,
        }
