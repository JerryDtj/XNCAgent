import json
import asyncio
from typing import AsyncIterator, Optional
from typing import Any, Coroutine

from xncagent.llm.llm_task import task_llm, task_llm_stream
from xncagent.llm.query_rewrite import query_rewrite, SCENE_WHITELIST
from xncagent.rdbms.message_repo import get_history_by_session, create_messages_batch
from xncagent.scene_matcher import match_scene
from xncagent.utils.logger import logger
from xncagent.utils.context import get_user_id
from xncagent.schemas.query_rewrite import RewriterQuestionResponse
from xncagent.rag.rag_retriever import retrieve_knowledge, save_user_msg
from xncagent.config.prompts import load_system_prompt
from xncagent.rdbms.session_repo import get_session, create_session, update_session_title_if_empty
from xncagent.utils.exceptions import UnauthorizedException
from xncagent.config import system_config


# 模块级：持有后台任务引用，防止被 GC 提前回收（asyncio 经典坑）
_bg_tasks: set[asyncio.Task] = set()
_TITLE_SYSTEM = (
    "你是会话标题生成器。根据用户的第一句话，输出不超过 12 字的简短标题，"
    "不要引号、不要句号、不要解释，只输出标题本身。"
)


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

def _ensure_session(session_id: Optional[int], user_id: str) -> tuple[Optional[int], Optional[str]]:
    """
    确保会话存在，返回 (session_id, title)。
    - 前端没传 session_id：新建，title 必然为空
    - 前端传了：校验归属，带出已有 title
    """
    if session_id is None:
        if user_id is None:
            return None, None
        new_id = create_session(user_id)
        logger.info(f"创建新的对话id: {new_id}")
        return new_id, None

    session = get_session(session_id, user_id)
    if session is None:
        logger.error(f"会话越权或不存在: session_id={session_id} requester={user_id}")
        raise UnauthorizedException("未找到该用户对应的会话")
    return session.id, session.title

def _prepare_turn(query: str,session_id: Optional[int]) -> dict:
    """
    预检、理解、检索，拼好生成消息。
    历史记录由原有的预检修改为合法请求都附带

    本地匹配也失败时 early 为 True，answer 是兜底话术，不再调用生成模型。
    """    
    # user_id一致,开始拿历史记录.准备把历史消息附带给llm
    history = ""
    if session_id is not None:
        history = get_history_by_session(session_id, turns=system_config["history"]["turns"])
        logger.info(f"history: {history}")
    # 定义是否走降级通道标志
    degraded = False
    try:
        # 开始走llm改写用户问题
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
                "query": query,
                "session_id": session_id,
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
        "query": query,
        "session_id": session_id,
    }


def _payload(session_id: int, prepared: dict, answer: str) -> dict:
    """
    失败消息构建
    """
    return {
        "answer": answer,
        "rewritten_query": prepared["rewritten_query"],
        "scene": prepared["scene"],
        "emotion_alert": prepared["emotion_alert"],
        "confidence": prepared["confidence"],
        "degraded": prepared["degraded"],
        "session_id": session_id,
    }


def _title_save(prepared: dict, answer: str) -> None:
    """
    消息保存。
    """
    user_id = prepared["user_id"]
    session_id = prepared.get("session_id")
    if user_id is None or session_id is None or answer is None:
       return 
    # 1) 标题：不依赖落库，独立起任务
    _fire_and_forget(_generate_title(session_id, prepared["query"]))

def _remember(prepared: dict, answer: str, title: Optional[str]) -> None:
    """
    一轮结束后，异步做三件事（全部 fire-and-forget，不影响主流程）：
      1. 仅当本会话 title 仍为空时生成标题；失败则保持空，下次再试
      2. 落库 user + assistant 两行
      3. 把这两行 embedding 进 chroma chat_history
    2 和 3 有依赖（embedding 要 message_id），放同一个任务里顺序执行。
    """
    if not (title or "").strip():
        _title_save(prepared, answer)
    _fire_and_forget(_persist_and_embed(prepared, answer))


def _fire_and_forget(coro: Coroutine[Any, Any, None]) -> None:
    """
    把 coro 扔进后台队列，不阻塞主流程。
    """
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(lambda t: _bg_tasks.discard(t))

async def _generate_title(session_id: int, query: str) -> None:
    """
    生成会话标题。
    """
    messages = [
        {"role": "system", "content": _TITLE_SYSTEM},
        {"role": "user", "content": query},
    ]
    try:
        result = await task_llm(messages)
        title = (result.get("answer") or "").strip().strip("「」\"'《》")[:12]
        if not title:
            logger.error(f"生成会话标题失败: {session_id}")
            return
        # 乐观锁：只有 title 还是空的时候才更新（并发下只有一个赢）
        ok = update_session_title_if_empty(session_id, title)
        logger.info(f"[remember] 标题生成 session_id={session_id} title={title!r} updated={ok}")
    except Exception as e:
        logger.exception(f"[remember] 标题生成失败 session_id={session_id}")
    return None

async def _persist_and_embed(prepared: dict, answer: str) -> None:
    """
    落库+embedding。
    """

    session_id = prepared["session_id"]
    user_id = prepared["user_id"]

    # 1. 批量落库（user + assistant 一次 INSERT）
    try:
        user_msg_id, assistant_msg_id = create_messages_batch(
            session_id,
            user_id,
            prepared["query"],
            answer,
            prepared["scene"],
            prepared["rewritten_query"],
            prepared["degraded"],
            prepared["emotion_alert"],
        )
        logger.info(f"[remember] 落库+embedding session_id={session_id} user_msg_id={user_msg_id} assistant_msg_id={assistant_msg_id}")
    except Exception as e:
        logger.exception(f"[remember] 落库+embedding失败 session_id={session_id}")
        return
    
    # 如果llm和本地调用都失败进入兜底,那么就没有必要在入chroma
    if not prepared["early"]:
        # 2. embedding：bge 是同步阻塞调用，丢线程池，别堵 event loop
        try:
            await asyncio.to_thread(save_user_msg,
                session_id, user_id, user_msg_id, assistant_msg_id,
                prepared["query"], answer
                )
        except Exception as e:
            logger.exception(f"[remember] embedding失败 session_id={session_id}")
            return

    return None

def _sse(data: dict | str) -> str:
    if isinstance(data, str):
        return f"data: {data}\n\n"
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

async def _check_user_id() -> str:
    """
        为空的几种情况:
            请求没走网关，直接打到 Python（本机 18000、脚本、测试）。浏览器里已经登录也没用，这个进程看不到 token。
            直接打到 Python，但 X-User-Id 缺失、不是整数，或者小于等于 0。中间件会把它收成 None。
            在请求之外调用 get_user_id()，比如新开的线程，或请求已经结束的后台任务。ContextVar 不会跟到这些地方，默认就是 None。
        不论哪一种,直接到这里为空都是不合理的,所以这里直接拒绝掉
    """
    user_id = get_user_id()
    logger.info(f"user_id: {user_id}")
    if user_id is None:
        
        logger.error("_check_user_id没有获取到用户id,拒绝请求")
        raise UnauthorizedException("用户未登录")
    return user_id


async def task(query: str, session_id: Optional[int]) -> Optional[dict]:
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
    user_id = await _check_user_id()
    session_id, title = _ensure_session(session_id, user_id)
    prepared = _prepare_turn(query, session_id)
    prepared["user_id"] = user_id
    if prepared["early"]:
        # llm和本地调用都失败,走兜底话术,并且保存消息
        _remember(prepared, prepared["answer"], title)
        return _payload(session_id, prepared, prepared["answer"])

    task_result = await task_llm(prepared["messages"])
    answer = task_result["answer"]
    _remember(prepared, answer, title)
    return _payload(session_id, prepared, answer)


async def task_stream(query: str, session_id: Optional[int]) -> AsyncIterator[str]:
    """
    与 task 同一套预检、检索和记历史，生成改为 task_llm_stream。

    """
    user_id = await _check_user_id()
    session_id, title = _ensure_session(session_id, user_id)
    prepared = _prepare_turn(query, session_id)
    prepared["user_id"] = user_id
    meta = _payload(session_id, prepared, prepared["answer"] if prepared["early"] else "")
    meta.pop("answer")
    yield _sse(meta)

    if prepared["early"]:
        # 全部失败,走兜底话术
        _remember(prepared, prepared["answer"], title)
        yield _sse({"text": prepared["answer"]})
        yield _sse("[DONE]")
        return

    parts: list[str] = []
    try:
        async for delta in task_llm_stream(prepared["messages"], temperature=0.5):
            parts.append(delta)
            yield _sse({"text": delta})
        _remember(prepared, "".join(parts), title)
        yield _sse("[DONE]")
    except Exception as e:
        logger.exception("[LLM] 流式生成异常")
        yield _sse({"error": str(e)})
