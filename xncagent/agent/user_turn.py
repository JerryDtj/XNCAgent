"""同一用户同时只允许一轮对话。

一期：进程内 ``dict[user_id, asyncio.Lock]``，拿不到立即失败，不排队、不等候。
二期多实例时，只替换 ``acquire_user_turn`` / ``release_user_turn`` 的实现为
Redis ``SET key NX EX``，调用方保持这两个函数的签名，零改动。
"""

import asyncio
from typing import Optional

# 固定拒绝话术。调用方在 acquire 返回 False 时原样返回，不落库、不检索。
TURN_BUSY_REPLY = "主子稍安勿躁，奴才正在回上一句呢，半盏茶后就到！"

_locks: dict[int, asyncio.Lock] = {}
_guard = asyncio.Lock()


async def acquire_user_turn(user_id: Optional[int]) -> bool:
    """尝试占用该用户的一轮对话。

    拿到返回 True。该用户已有一轮在进行时立即返回 False。
    user_id 为空（匿名）不加锁，直接返回 True。
    """
    if user_id is None:
        return True
    async with _guard:
        lock = _locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            _locks[user_id] = lock
        if lock.locked():
            return False
        # 锁空闲时 acquire 不会挂起，这里不会把 _guard 让给别的请求。
        await lock.acquire()
        return True


def release_user_turn(user_id: Optional[int]) -> None:
    """释放该用户的一轮。匿名或并未占用时不做任何事。"""
    if user_id is None:
        return
    lock = _locks.get(user_id)
    if lock is not None and lock.locked():
        lock.release()
