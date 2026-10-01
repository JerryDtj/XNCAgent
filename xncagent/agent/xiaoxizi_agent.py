import json
import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator, Optional
from typing import Any, Coroutine

from xncagent.llm.llm_task import task_llm, task_llm_stream
from xncagent.llm.query_rewrite import query_rewrite, SCENE_WHITELIST
from xncagent.rdbms.message_repo import (
    get_history_by_session,
    create_messages_batch,
    list_messages_for_summary,
    get_messages_by_ids,
)
from xncagent.scene_matcher import match_scene
from xncagent.utils.logger import logger
from xncagent.utils.context import get_user_id
from xncagent.schemas.query_rewrite import RewriterQuestionResponse
from xncagent.rag.rag_retriever import (
    retrieve_knowledge,
    save_user_msg,
    save_session_summary,
    retrieve_user_vectors,
    _HISTORY_COLLECTION,
)
from xncagent.config.prompts import load_system_prompt
from xncagent.rdbms.session_repo import (
    get_session,
    create_session,
    update_session_title_if_empty,
    list_sessions,
    get_session_message_count,
    get_summary_covered_count,
    upsert_session_summary,
    get_sessions_by_ids,
)
from xncagent.utils.exceptions import UnauthorizedException
from xncagent.config import system_config


# 模块级：持有后台任务引用，防止被 GC 提前回收（asyncio 经典坑）
_bg_tasks: set[asyncio.Task] = set()
_summary_locks: dict[int, asyncio.Lock] = {}
_summary_locks_guard = asyncio.Lock()
_TITLE_SYSTEM = (
    "你是会话标题生成器。根据用户的第一句话，输出不超过 12 字的简短标题，"
    "不要引号、不要句号、不要解释，只输出标题本身。"
)
_SUMMARY_SYSTEM = "总结这个会话主人和小喜子聊了什么，≤100 字，只输出摘要正文。"
_SUMMARY_UNCOVERED = 10
_SUMMARY_MAX_CHARS = 100
_RECALL_TOP_K = 5


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
    memory: str = "",
) -> list[dict[str, str]]:
    """
    构建生成消息。话术参考和旧会话记忆附在系统提示词后面，历史和本轮输入放在用户消息里。
    安全树洞放在记忆之后，优先级高于记忆内容。
    """
    system = load_system_prompt("xiaoxizi_system")
    if knowledge:
        system += (
            "\n\n# 本轮话术参考\n"
            "下面是按当前场景从知识库检索到的话术。借口气和招式，用自己的话说，"
            "不要逐字复读，也不要提及知识库。\n"
            f"{knowledge}"
        )
    if memory:
        system += (
            "\n\n# 旧会话记忆\n"
            "下面是主人在别的会话里说过的原话。只根据这些原话回忆；"
            "没写到的事不要编，老实说不记得。\n"
            f"{memory}"
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

def _relative_time(value: Any) -> str:
    if not isinstance(value, datetime):
        return "刚刚"
    # 无时区的旧值按 UTC 钟面理解；TIMESTAMPTZ 读出来带时区，按它自己的时区比。
    if value.tzinfo is None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        now = datetime.now(value.tzinfo)
    minutes = int((now - value).total_seconds() // 60)
    if minutes < 1:
        return "刚刚"
    if minutes < 60:
        return f"{minutes} 分钟前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} 小时前"
    return f"{hours // 24} 天前"


def _format_recall_block(hits: list) -> str:
    """按 session_id 批量查标题，拼成带来源和相对时间的记忆块。"""
    session_ids: list[int] = []
    message_ids: list[int] = []
    for hit in hits:
        session_id = int(hit.node.metadata["session_id"])
        if session_id not in session_ids:
            session_ids.append(session_id)
        message_id = hit.node.metadata.get("message_id")
        if message_id is not None:
            message_ids.append(int(message_id))
    sessions = get_sessions_by_ids(session_ids)
    messages = get_messages_by_ids(message_ids)

    grouped: dict[int, list] = {}
    order: list[int] = []
    for hit in hits:
        session_id = int(hit.node.metadata["session_id"])
        if session_id not in sessions:
            continue
        if session_id not in grouped:
            order.append(session_id)
            grouped[session_id] = []
        grouped[session_id].append(hit)

    blocks: list[str] = []
    for session_id in order:
        rows = grouped[session_id]
        rows.sort(key=lambda hit: int(hit.node.metadata.get("message_id") or 0))
        title = sessions[session_id].get("title") or "未命名会话"
        times = []
        lines = []
        for hit in rows:
            meta = hit.node.metadata
            message_id = meta.get("message_id")
            created_at = None
            if message_id is not None:
                created_at = (messages.get(int(message_id)) or {}).get("created_at")
            if created_at is not None:
                times.append(created_at)
            role = "小喜子" if meta.get("role") == "assistant" else "主人"
            lines.append(f"{role}: {hit.node.get_content().strip()}")
        when = _relative_time(min(times) if times else sessions[session_id].get("created_at"))
        blocks.append(f"（来自会话《{title}》，{when}）\n" + "\n".join(lines))
    return "\n\n".join(blocks)


def _recall_memory(recall_query: str, user_id: Optional[int], degraded: bool) -> str:
    """
    recall_query 非空、有 user_id、且不是降级轮时，查 chat_history top 5。
    为空或失败只记日志，返回空串。
    """
    if degraded or user_id is None or not recall_query:
        logger.info(
            f"[recall] 未触发检索 recall_query={recall_query!r} "
            f"user_id={user_id} degraded={degraded}"
        )
        return ""
    try:
        hits = retrieve_user_vectors(
            recall_query,
            user_id,
            collections=(_HISTORY_COLLECTION,),
            top_k=_RECALL_TOP_K,
        )
        if not hits:
            logger.info(f"[recall] 检索无命中 recall_query={recall_query!r}")
            return ""
        block = _format_recall_block(hits)
    except Exception:
        logger.exception(f"[recall] 检索失败 recall_query={recall_query!r}")
        return ""
    if not block:
        logger.info(f"[recall] 命中无法对应到会话 recall_query={recall_query!r}")
        return ""
    logger.info(
        f"[recall] 注入旧会话记忆 hits={len(hits)} "
        f"recall_query={recall_query!r} block={block[:200]!r}"
    )
    return block


def _prepare_turn(query: str, session_id: Optional[int], user_id: Optional[int] = None) -> dict:
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
    recall_query = ""
    try:
        # 开始走llm改写用户问题
        check_result: RewriterQuestionResponse = _understand(query, history)
        rewritten_query = check_result.rewritten_query
        scene = check_result.scene
        emotion_alert = check_result.emotion_alert
        confidence = check_result.confidence
        recall_query = (check_result.recall_query or "").strip()
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

    memory = _recall_memory(recall_query, user_id, degraded)
    return {
        "early": False,
        "messages": _build_messages(query, history, knowledge_result, emotion_alert, memory),
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
    一轮结束后，异步做这些事（全部 fire-and-forget，不影响主流程）：
      1. 仅当本会话 title 仍为空时生成标题；失败则保持空，下次再试
      2. 落库 user + assistant 两行
      3. 把这两行 embedding 进 chroma chat_history
      4. 未覆盖消息数 >= 10 且该轮非降级时，另起任务刷新会话摘要
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


async def _summary_lock(session_id: int) -> asyncio.Lock:
    async with _summary_locks_guard:
        lock = _summary_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            _summary_locks[session_id] = lock
        return lock


def _schedule_summary_refresh(session_id: int, user_id: int, degraded: bool) -> None:
    """未覆盖消息数 >= 10 且该轮非降级时，另起任务刷新摘要。检查失败只记日志。"""
    if degraded:
        return
    try:
        current = get_session_message_count(session_id)
        covered = get_summary_covered_count(session_id)
    except Exception:
        logger.exception(f"[summary] 检查未覆盖消息数失败 session_id={session_id}")
        return
    if current - covered < _SUMMARY_UNCOVERED:
        return
    _fire_and_forget(_refresh_session_summary(session_id, user_id))


async def _refresh_session_summary(session_id: int, user_id: int) -> None:
    """
    进 per-session 锁后重查未覆盖数，已被别的任务刷过则跳过。
    全量重摘，失败只记日志，不抛回调用方。
    """
    lock = await _summary_lock(session_id)
    async with lock:
        try:
            current = get_session_message_count(session_id)
            covered = get_summary_covered_count(session_id)
            if current - covered < _SUMMARY_UNCOVERED:
                logger.info(
                    f"[summary] 跳过 session_id={session_id} "
                    f"current={current} covered={covered}"
                )
                return
            text, covered_now = list_messages_for_summary(session_id, user_id)
            if not text.strip():
                logger.error(f"[summary] 会话没有可摘要的消息 session_id={session_id}")
                return
            result = await task_llm(
                [
                    {"role": "system", "content": _SUMMARY_SYSTEM},
                    {"role": "user", "content": text},
                ],
                temperature=0.3,
            )
            summary = (result.get("answer") or "").strip()[:_SUMMARY_MAX_CHARS]
            if not summary:
                logger.error(f"[summary] 摘要为空 session_id={session_id}")
                return
            await asyncio.to_thread(save_session_summary, session_id, user_id, summary)
            upsert_session_summary(session_id, user_id, summary, covered_now)
            logger.info(
                f"[summary] 已刷新 session_id={session_id} "
                f"covered={covered_now} summary={summary!r}"
            )
        except Exception:
            logger.exception(f"[summary] 刷新失败 session_id={session_id}")

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

    # 摘要刷新独立排队，不挡这条消息的 embedding，也不回主 SSE
    _schedule_summary_refresh(session_id, user_id, bool(prepared.get("degraded")))

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
    prepared = _prepare_turn(query, session_id, user_id)
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
    prepared = _prepare_turn(query, session_id, user_id)
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