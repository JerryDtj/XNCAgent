import asyncio
import json
from functools import lru_cache
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from xncagent.llm.config import AGICTOR_API_KEY, AGICTOR_BASE_URL, TASK_MODEL_NAME
from xncagent.utils.logger import logger

# 服务商返回负载过高时的重试间隔：第 1/2/3 次重试前分别等待 2s、4s、8s。
_LOAD_BACKOFF_SECONDS = (2, 4, 8)

_client: AsyncOpenAI | None = None

@lru_cache(maxsize=1)
def get_client() -> AsyncOpenAI:
    """
    获取OpenAI客户端
    :return: OpenAI客户端
    """
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=AGICTOR_API_KEY, base_url=AGICTOR_BASE_URL,timeout=60)
    return _client

def _is_provider_overloaded(exc: Exception) -> bool:
    return "Service load is too high" in str(exc)

async def _create_chat(client: AsyncOpenAI, **kwargs: Any):
    """发往服务商的 chat.completions.create，负载过高时最多重试 3 次。"""
    model = kwargs.get("model")
    stream = kwargs.get("stream", False)
    logger.info(f"[LLM] 发往服务商 model={model} stream={stream}")
    last_error: Exception | None = None
    for attempt in range(len(_LOAD_BACKOFF_SECONDS) + 1):
        if attempt:
            delay = _LOAD_BACKOFF_SECONDS[attempt - 1]
            logger.warning(
                f"[LLM] 服务商负载过高，{delay}s 后重试 "
                f"({attempt}/{len(_LOAD_BACKOFF_SECONDS)}) model={model}"
            )
            await asyncio.sleep(delay)
        try:
            return await client.chat.completions.create(**kwargs)
        except Exception as exc:
            last_error = exc
            if not _is_provider_overloaded(exc) or attempt == len(_LOAD_BACKOFF_SECONDS):
                raise
    raise last_error  # pragma: no cover

async def task_llm(
    messages: list[dict[str, str]],
    *,
    model: str = TASK_MODEL_NAME,
    temperature: float = 0.5,
    extra: dict[str, Any] | None = None,
) -> dict:
    """
    非流式调用 LLM
    :param messages: 消息
    :param model: 模型
    :param temperature: 温度
    :param extra: 额外参数
    :return: {"answer": str, "usage": dict | None, "raw": ChatCompletion}
    """
    client = get_client()
    key_words: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if extra:
        key_words.update(extra)
        key_words.pop("response_format", None)
    logger.info(f"[LLM] 非流式调用 model={model}, messages={len(messages)} 条")
    resp = await _create_chat(client, **key_words)
    answer = resp.choices[0].message.content or ""
    usage = resp.usage if resp.usage else None
    return {"answer": answer, "usage": usage, "raw": resp}

# ---------- 流式 ----------
async def task_llm_stream(
    messages: list[dict[str, str]],
    *,
    model: str = TASK_MODEL_NAME,
    temperature: float = 0.7,
    extra: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    """
    流式调用 LLM，逐段 yield 文本 delta（纯文本，不含 SSE 包装）。
    调用方自己决定怎么包装（SSE / WebSocket / 纯文本）。
    """
    client = get_client()
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},   # 最后一帧带 usage
    }
    if extra:
        kwargs.update(extra)
        kwargs.pop("response_format", None)

    logger.info(f"[LLM] 流式调用 model={model}, messages={len(messages)} 条")
    stream = await _create_chat(client, **kwargs)

    async for chunk in stream:
        if chunk.choices:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
        # 最后一帧 choices 为空，只带 usage
        if getattr(chunk, "usage", None):
            logger.info(f"[LLM] 流式完成, tokens={chunk.usage.model_dump()}")


# ---------- 流式 + SSE 包装（可选，省得路由里再拼） ----------
async def chat_completion_sse(
    messages: list[dict[str, str]],
    *,
    model: str = TASK_MODEL_NAME,
    temperature: float = 0.7,
) -> AsyncIterator[str]:
    """
    直接产出 SSE 格式字符串，路由里 yield 即可。
    """
    try:
        async for delta in task_llm_stream(
            messages, model=model, temperature=temperature
        ):
            yield f"data: {json.dumps({'text': delta}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    except Exception as e:
        logger.exception("[LLM] 流式异常")
        yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
