import json
import asyncio
import random
import time
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
from xncagent.rdbms.settings_repo import get_settings
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
from xncagent.agent.user_turn import TURN_BUSY_REPLY, acquire_user_turn, release_user_turn
# 启动时注册 play_music / get_joke
import xncagent.tools  # noqa: F401
from xncagent.tools.music_plugin import music_plugin
from xncagent.tools.registry import get as get_plugin


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

def _format_web_results(web_results: list) -> str:
    """设计 §3.4：标题、摘要、来源。空列表不该走到这里。"""
    lines = [
        "# 联网检索资料",
        "下面是奴才刚上网为主人查到的资料。回答时只依据这些资料说；",
        "资料没提到的，老实承认不知道，不要编。",
        "",
    ]
    for index, hit in enumerate(web_results, 1):
        if isinstance(hit, dict):
            title = hit.get("title") or ""
            snippet = hit.get("snippet") or ""
            url = hit.get("url") or ""
        else:
            title = getattr(hit, "title", "") or ""
            snippet = getattr(hit, "snippet", "") or ""
            url = getattr(hit, "url", "") or ""
        lines.append(f"{index}. 《{title}》")
        lines.append(f"   {snippet}")
        lines.append(f"   来源：{url}")
    return "\n".join(lines)


def _build_messages(
    query: str,
    history: str,
    knowledge: str,
    emotion_alert: bool,
    memory: str = "",
    web_results: list | None = None,
    cold_start: bool = False,
    needs_silence: bool = False,
) -> list[dict[str, str]]:
    """
    构建生成消息。话术参考和旧会话记忆附在系统提示词后面，历史和本轮输入放在用户消息里。
    块顺序：话术参考、联网资料、旧会话记忆、劝谏、树洞、冷启动。
    劝谏与树洞同轮只注入树洞。冷启动标记放在所有附加块的最后。
    """
    system = load_system_prompt("xiaoxizi_system")
    if knowledge:
        system += (
            "\n\n# 本轮话术参考\n"
            "下面是按当前场景从知识库检索到的话术。借口气和招式，用自己的话说，"
            "不要逐字复读，也不要提及知识库。\n"
            f"{knowledge}"
        )
    if web_results:
        system += "\n\n" + _format_web_results(web_results)
    if memory:
        system += (
            "\n\n# 旧会话记忆\n"
            "下面是主人在别的会话里说过的原话。只根据这些原话回忆；"
            "没写到的事不要编，老实说不记得。\n"
            f"{memory}"
        )
    # 两者皆真只注入树洞：主人明确要安静就安静，不再劝。
    if needs_silence:
        system += (
            "\n\n# 本轮安全树洞\n"
            "奴才之前的表演一直没接住主人的情绪。只做三件事："
            "说奴才把嘴闭上了、递上虚拟纸巾、安静陪着直到主人重新开口。不要劝，不要问。"
        )
    elif emotion_alert:
        system += (
            "\n\n# 本轮劝谏\n"
            "主人表达了重度痛苦。收起全部玩笑，认真温和地劝：先接住情绪，再劝主人"
            "找信得过的真人诉说，必要时寻求专业帮助。可以问主子愿意多说说吗。"
        )
    if cold_start:
        system += (
            "\n\n# 本轮冷启动\n"
            "这是本会话的第一轮。按规则 5 输出开场白（整场只允许这一次）。"
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


def _is_cold_start(session_id: Optional[int]) -> bool:
    """第一轮生成前消息还没落库，计数为 0。查数失败按非冷启动，不阻断。"""
    if session_id is None:
        return False
    try:
        return get_session_message_count(session_id) == 0
    except Exception as exc:
        logger.warning(f"[cold_start] 查询消息数失败 session_id={session_id} error={exc}")
        return False


def _log_web_search_skipped(query: str, provider: str, scene: str, needs: bool) -> None:
    logger.info(
        f"[web_search] 判定 query={query} needs={str(needs).lower()} scene={scene} "
        f"provider={provider} hits=0 cost_ms=0"
    )


def _web_failure_is_config(error: str) -> bool:
    text = error or ""
    return "未配置" in text or "未注册" in text or "未知 provider" in text


async def _search_web(query: str, scene: str) -> list[dict]:
    """原始 query 调插件。超时、空结果、缺 key 都只记日志，返回空列表。"""
    plugin = get_plugin("web_search")
    provider = getattr(plugin, "provider_name", "unknown") if plugin else "unknown"
    if plugin is None:
        logger.error(f"[web_search] 失败 query={query} error=插件未注册")
        return []
    try:
        count = int(getattr(plugin, "count", 8) or 8)
    except (TypeError, ValueError):
        count = 8
    started = time.perf_counter()
    try:
        result = await asyncio.to_thread(plugin.run, {"query": query, "count": count})
    except Exception as exc:
        logger.warning(f"[web_search] 失败 query={query} error={exc}")
        return []
    cost_ms = int((time.perf_counter() - started) * 1000)
    if result is None or not getattr(result, "ok", False):
        err = getattr(result, "error", "") or "搜索失败"
        line = f"[web_search] 失败 query={query} error={err}"
        if _web_failure_is_config(err):
            logger.error(line)
        else:
            logger.warning(line)
        return []
    hits = (getattr(result, "data", None) or {}).get("hits") or []
    if not isinstance(hits, list):
        hits = []
    logger.info(
        f"[web_search] 判定 query={query} needs=true scene={scene} "
        f"provider={provider} hits={len(hits)} cost_ms={cost_ms}"
    )
    return hits


async def _prepare_turn(query: str, session_id: Optional[int], user_id: Optional[int] = None) -> dict:
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
    check_result: Optional[RewriterQuestionResponse] = None
    try:
        # 开始走llm改写用户问题
        check_result = _understand(query, history)
        rewritten_query = check_result.rewritten_query
        scene = check_result.scene
        emotion_alert = check_result.emotion_alert
        confidence = check_result.confidence
        recall_query = (check_result.recall_query or "").strip()
        needs_silence = bool(check_result.needs_silence)
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
                "music": None,
            }
        rewritten_query = query
        emotion_alert = False
        needs_silence = False
        confidence = score
        degraded = True

    if scene in SCENE_WHITELIST:
        knowledge_result = retrieve_knowledge(rewritten_query, scene)
    else:
        logger.info(f"场景 [{scene}] 不在检索白名单，跳过知识库")
        knowledge_result = ""

    memory = _recall_memory(recall_query, user_id, degraded)
    music = _maybe_play_music(
        scene=scene,
        emotion_alert=emotion_alert,
        user_id=user_id,
        session_id=session_id,
    )
    # 降级、劝谏、树洞都不打外部搜索。树洞优先于劝谏。
    if needs_silence:
        logger.info(
            f"[needs_silence] 判定 query={query} needs=true scene={scene} "
            f"emotion_alert={str(emotion_alert).lower()}"
        )
    if degraded or check_result is None:
        logger.info(f"[web_search] 跳过 query={query} reason=degraded")
        web_results: list[dict] = []
    elif needs_silence or emotion_alert:
        reason = "needs_silence" if needs_silence else "emotion_alert"
        logger.info(f"[web_search] 跳过 query={query} reason={reason}")
        web_results = []
    elif check_result.needs_web_search:
        web_results = await _search_web(query, scene)
    else:
        plugin = get_plugin("web_search")
        provider = getattr(plugin, "provider_name", "unknown") if plugin else "unknown"
        _log_web_search_skipped(query, provider, scene, False)
        web_results = []
    cold_start = _is_cold_start(session_id)
    return {
        "early": False,
        "messages": _build_messages(
            query,
            history,
            knowledge_result,
            emotion_alert,
            memory,
            web_results,
            cold_start,
            needs_silence,
        ),
        "rewritten_query": rewritten_query,
        "scene": scene,
        "emotion_alert": emotion_alert,
        "confidence": confidence,
        "degraded": degraded,
        "query": query,
        "session_id": session_id,
        "music": music,
    }


def _maybe_play_music(
    scene: str,
    emotion_alert: bool,
    user_id: Optional[int],
    session_id: Optional[int],
) -> Optional[dict]:
    """
    场景音乐触发（生成前，与召回同级）。判断顺序严格按设计稿 §2：
      1. emotion_alert → 跳过
      2. scene 未配置音乐（含无关闲聊）→ 跳过
      3. 概率抽签未中 → 跳过
      4. user_settings.music_enabled=false → 跳过
         （匿名按默认 true；查询失败 WARN 并按 true 放行）
      5. 调 play_music；成功返回 data，失败只记日志
    任何异常只记日志，绝不阻断主链路。
    """
    try:
        if emotion_alert:
            logger.info("[music] 跳过：emotion_alert 安全树洞")
            return None
        if not music_plugin.scene_configured(scene):
            logger.info(f"[music] 跳过：场景未配置音乐 scene={scene!r}")
            return None
        # 曲库损坏时跳过抽签，直接走后续设置检查 + run，保证失败进日志（验收用例 7）
        if not music_plugin.load_failed:
            prob = music_plugin.probability_for(scene)
            draw = random.random()
            if draw >= prob:
                logger.info(
                    f"[music] 跳过：概率未中 scene={scene!r} "
                    f"prob={prob} draw={draw:.4f}"
                )
                return None

        music_enabled = True
        if user_id is None:
            logger.info("[music] 匿名用户，按默认 music_enabled=true")
        else:
            try:
                row = get_settings(user_id)
                music_enabled = bool(row["music_enabled"])
            except Exception:
                logger.warning(
                    f"[music] 设置查询失败，按默认 true 放行 user_id={user_id}"
                )
                music_enabled = True
        if not music_enabled:
            logger.info(f"[music] 跳过：用户关闭音乐 user_id={user_id}")
            return None

        plugin = get_plugin("play_music")
        if plugin is None:
            logger.error("[music] play_music 插件未注册")
            return None
        result = plugin.run({
            "scene": scene,
            "session_key": str(session_id) if session_id is not None else f"u:{user_id}",
        })
        if not result.ok:
            logger.error(f"[music] 插件失败: {result.error}")
            return None
        logger.info(
            f"[music] 命中 scene={scene!r} title={result.data.get('title')!r} "
            f"url={result.data.get('url')!r}"
        )
        return result.data
    except Exception:
        logger.exception("[music] 触发链路异常，已忽略")
        return None


def _payload(session_id: int, prepared: dict, answer: str) -> dict:
    """
    失败消息构建
    """
    payload = {
        "answer": answer,
        "rewritten_query": prepared["rewritten_query"],
        "scene": prepared["scene"],
        "emotion_alert": prepared["emotion_alert"],
        "confidence": prepared["confidence"],
        "degraded": prepared["degraded"],
        "session_id": session_id,
    }
    return payload


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


def _persist_interrupted(prepared: dict, answer: str, title: Optional[str]) -> None:
    """客户端断开时同步落库已生成的部分。只记 INFO，不把断连当成错误。"""
    session_id = prepared.get("session_id")
    user_id = prepared.get("user_id")
    if user_id is None or session_id is None:
        return
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
            interrupted=True,
        )
    except Exception as exc:
        logger.info(f"[turn] 客户端断开，部分回复落库失败 session_id={session_id} error={exc}")
        return
    logger.info(
        f"[turn] 客户端断开，已保存部分回复 session_id={session_id} "
        f"user_msg_id={user_msg_id} assistant_msg_id={assistant_msg_id} "
        f"chars={len(answer)} interrupted=true"
    )
    if not (title or "").strip():
        _title_save(prepared, answer)


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

def _sse(data: dict | str, event: Optional[str] = None) -> str:
    """拼 SSE 帧。event 非空时输出 event: 行（如 meta）；默认事件不写 event 行。"""
    if isinstance(data, str):
        payload = data
    else:
        payload = json.dumps(data, ensure_ascii=False)
    if event:
        return f"event: {event}\ndata: {payload}\n\n"
    return f"data: {payload}\n\n"


def _sse_music_meta(music: Optional[dict]) -> Optional[str]:
    """本轮有音乐时返回 meta 帧，否则 None（不发送）。"""
    if not music:
        return None
    return _sse({"music": music}, event="meta")


def _busy_payload(session_id: Optional[int]) -> dict:
    return {
        "answer": TURN_BUSY_REPLY,
        "rewritten_query": "",
        "scene": "",
        "emotion_alert": False,
        "confidence": 0.0,
        "degraded": False,
        "session_id": session_id,
    }


def _log_turn_rejected(user_id: int) -> None:
    logger.info(f"[turn] user_id={user_id} 上一轮未结束，拒绝本轮")

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
    # 匿名在 _check_user_id 里已经拒绝。这里 user_id 必有值，占用失败立即回固定话术。
    if not await acquire_user_turn(user_id):
        _log_turn_rejected(user_id)
        return _busy_payload(session_id)
    try:
        session_id, title = _ensure_session(session_id, user_id)
        prepared = await _prepare_turn(query, session_id, user_id)
        prepared["user_id"] = user_id
        if prepared["early"]:
            # llm和本地调用都失败,走兜底话术,并且保存消息
            _remember(prepared, prepared["answer"], title)
            return _payload(session_id, prepared, prepared["answer"])

        task_result = await task_llm(prepared["messages"])
        answer = task_result["answer"]
        _remember(prepared, answer, title)
        return _payload(session_id, prepared, answer)
    finally:
        release_user_turn(user_id)


async def task_stream(query: str, session_id: Optional[int]) -> AsyncIterator[str]:
    """
    与 task 同一套预检、检索和记历史，生成改为 task_llm_stream。

    """
    user_id = await _check_user_id()
    # 匿名在 _check_user_id 里已经拒绝。占用失败不检索、不调模型，直接把固定话术写进流。
    if not await acquire_user_turn(user_id):
        _log_turn_rejected(user_id)
        yield _sse({"text": TURN_BUSY_REPLY})
        yield _sse("[DONE]")
        return
    try:
        session_id, title = _ensure_session(session_id, user_id)
        prepared = await _prepare_turn(query, session_id, user_id)
        prepared["user_id"] = user_id
        meta = _payload(session_id, prepared, prepared["answer"] if prepared["early"] else "")
        meta.pop("answer")
        yield _sse(meta)

        music_frame = _sse_music_meta(prepared.get("music"))
        if music_frame:
            yield music_frame

        if prepared["early"]:
            # 全部失败,走兜底话术
            _remember(prepared, prepared["answer"], title)
            yield _sse({"text": prepared["answer"]})
            yield _sse("[DONE]")
            return

        parts: list[str] = []
        finished = False
        try:
            async for delta in task_llm_stream(prepared["messages"], temperature=0.5):
                parts.append(delta)
                yield _sse({"text": delta})
            finished = True
            _remember(prepared, "".join(parts), title)
            yield _sse("[DONE]")
        except (GeneratorExit, asyncio.CancelledError):
            if not finished:
                _persist_interrupted(prepared, "".join(parts), title)
            raise
        except Exception as e:
            logger.exception("[LLM] 流式生成异常")
            yield _sse({"error": str(e)})
    finally:
        release_user_turn(user_id)