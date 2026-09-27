import json
from pathlib import Path
from typing import AsyncIterator, Optional, Set

import yaml

from xncagent.llm.llm_task import get_history_by_user_id, save_history, task_llm, task_llm_stream
from xncagent.llm.query_rewrite import query_rewrite, SCENE_WHITELIST
from xncagent.scene_matcher import match_scene
from xncagent.utils.logger import logger
from xncagent.utils.context import get_user_id
from xncagent.schemas.query_rewrite import RewriterQuestionResponse
from xncagent.rag.rag_retriever import retrieve_knowledge
from xncagent.config.prompts import load_system_prompt



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

def _build_messages(
    query: str,
    history: str,
    knowledge: str,
    emotion_alert: bool,
) -> list[dict[str, str]]:
    """
    构建生成消息。话术参考附在系统提示词后面，历史和本轮输入放在用户消息里。
    """
    system = load_system_prompt("xiaoxizi_system")
    if knowledge:
        system += (
            "\n\n# 本轮话术参考\n"
            "下面是按当前场景从知识库检索到的话术。借口气和招式，用自己的话说，"
            "不要逐字复读，也不要提及知识库。\n"
            f"{knowledge}"
        )
    if emotion_alert:
        system += (
            "\n\n# 本轮安全树洞\n"
            "这一轮情绪预警已触发。立刻停掉玩笑：说奴才把嘴闭上了，"
            "递上虚拟纸巾，安静陪着，不要接梗。"
        )
    user_content = query
    if history:
        user_content = f"## 历史对话\n{history}\n\n## 主人这句\n{query}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]

_FALLBACK_ANSWER = "奴才这会儿脑子转不过来，主子稍等片刻，奴才缓过神再来逗您。"


def _prepare_turn(query: str) -> dict:
    """
    预检、理解、检索，拼好生成消息。

    本地匹配也失败时 early 为 True，answer 是兜底话术，不再调用生成模型。
    """
    user_id = get_user_id()
    history = ""
    if need_rewrite(query) and user_id is not None:
        history = get_history_by_user_id(user_id)

    degraded = False
    try:
        check_result: RewriterQuestionResponse = _understand(query, history)
        rewritten_query = check_result.rewritten_query
        scene = check_result.scene
        emotion_alert = check_result.emotion_alert
        confidence = check_result.confidence
    except Exception as e:
        logger.error(f"LLM 统一调用失败，降级为本地场景匹配: {e}")
        try:
            scene, score = match_scene(query)
        except Exception as match_error:
            logger.error(f"本地场景匹配也失败，返回兜底话术: {match_error}")
            return {
                "early": True,
                "answer": _FALLBACK_ANSWER,
                "rewritten_query": query,
                "scene": "无关闲聊",
                "emotion_alert": False,
                "confidence": 0.0,
                "degraded": True,
                "user_id": None,
                "query": query,
            }
        rewritten_query = query
        emotion_alert = False
        confidence = score
        degraded = True

    if scene in SCENE_WHITELIST:
        knowledge_result = retrieve_knowledge(rewritten_query, scene)
    else:
        logger.info(f"场景 [{scene}] 不在检索白名单，跳过知识库")
        knowledge_result = ""

    return {
        "early": False,
        "messages": _build_messages(query, history, knowledge_result, emotion_alert),
        "rewritten_query": rewritten_query,
        "scene": scene,
        "emotion_alert": emotion_alert,
        "confidence": confidence,
        "degraded": degraded,
        "user_id": user_id,
        "query": query,
    }


def _payload(prepared: dict, answer: str) -> dict:
    return {
        "answer": answer,
        "rewritten_query": prepared["rewritten_query"],
        "scene": prepared["scene"],
        "emotion_alert": prepared["emotion_alert"],
        "confidence": prepared["confidence"],
        "degraded": prepared["degraded"],
    }


def _remember(prepared: dict, answer: str) -> None:
    user_id = prepared["user_id"]
    if user_id is not None and answer:
        save_history(user_id, prepared["query"], answer)


def _sse(data: dict | str) -> str:
    if isinstance(data, str):
        return f"data: {data}\n\n"
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def task(query: str) -> Optional[dict]:
    """
    预检、理解、检索、生成，并记下这一轮。

    :param query: 用户原始输入
    :return: {
        "answer": str,
        "rewritten_query": str,
        "scene": str,        # 6 值之一：5 个场景白名单 + "无关闲聊"
        "emotion_alert": bool,
        "confidence": float,
        "degraded": bool,    # True 表示 scene 来自本地 bge，不是 LLM
    }
    主路径：预检决定带不带历史，LLM 统一调用（改写 + 场景 + 情绪预警），
    按 LLM 的 scene 检索。交叉校验只记日志，不改 scene。
    降级通道：LLM 失败时用 match_scene 的 scene 继续检索；
    场景是「无关闲聊」时跳过检索。bge 也失败才返回兜底话术。
    """
    prepared = _prepare_turn(query)
    if prepared["early"]:
        return _payload(prepared, prepared["answer"])

    task_result = await task_llm(prepared["messages"])
    answer = task_result["answer"]
    _remember(prepared, answer)
    return _payload(prepared, answer)


async def task_stream(query: str) -> AsyncIterator[str]:
    """
    与 task 同一套预检、检索和记历史，生成改为 task_llm_stream。

    先推一帧元数据，再逐段推文本，最后一帧为 [DONE]。
    """
    prepared = _prepare_turn(query)
    meta = _payload(prepared, prepared["answer"] if prepared["early"] else "")
    meta.pop("answer")
    yield _sse(meta)

    if prepared["early"]:
        yield _sse({"text": prepared["answer"]})
        yield _sse("[DONE]")
        return

    parts: list[str] = []
    try:
        async for delta in task_llm_stream(prepared["messages"], temperature=0.5):
            parts.append(delta)
            yield _sse({"text": delta})
        _remember(prepared, "".join(parts))
        yield _sse("[DONE]")
    except Exception as e:
        logger.exception("[LLM] 流式生成异常")
        yield _sse({"error": str(e)})
