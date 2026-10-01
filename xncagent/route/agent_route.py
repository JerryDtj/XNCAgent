from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import StreamingResponse

from xncagent.agent.xiaoxizi_agent import task, task_stream
from xncagent.rdbms.message_repo import list_messages, list_messages_around
from xncagent.rdbms.session_repo import (
    delete_session,
    get_session,
    list_sessions,
    update_session_title,
)
from xncagent.schemas.chat_session import ChatSession
from xncagent.utils.context import get_user_id
from xncagent.rag.rag_retriever import delete_session_vectors, search_sessions


agent_route = APIRouter(prefix="/agent", tags=["agent"])


@agent_route.post("/chat")
async def chat(
    message: str = Body(embed=True),
    session_id: Optional[int] = Body(None, embed=True),
):
    return await task(message, session_id)


@agent_route.post("/chat/stream")
async def chat_stream(
    message: str = Body(embed=True),
    session_id: Optional[int] = Body(None, embed=True),
):
    return StreamingResponse(
        task_stream(message, session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _owned_session(session_id: int) -> tuple[int, ChatSession]:
    user_id = get_user_id()
    # 匿名没有 user_id，不查具体会话
    if user_id is None:
        raise HTTPException(status_code=404, detail="资源未找到")
    session = get_session(session_id, user_id)
    if session is None:
        raise HTTPException(status_code=404, detail="资源未找到")
    return user_id, session


@agent_route.get("/sessions")
def list_sessions_api(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    user_id = get_user_id()
    if user_id is None:
        return {"items": [], "total": 0}
    rows, total = list_sessions(user_id, page, page_size)
    items = [
        {
            "id": row["id"],
            "title": row["title"] if row["title"] is not None else "",
            "summary": row["summary"] if row["summary"] is not None else "",
            "message_count": row["message_count"],
            "last_message_at": row["last_message_at"],
        }
        for row in rows
    ]
    return {"items": items, "total": total}


@agent_route.patch("/sessions/{id}")
def rename_session_api(id: int, title: str = Body(embed=True)):
    title = title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="标题不能为空")
    _owned_session(id)
    update_session_title(id, title)
    _user_id, session = _owned_session(id)
    return {"id": id, "title": session.title}


@agent_route.delete("/sessions/{id}")
def delete_session_api(id: int):
    _owned_session(id)
    delete_session_vectors(id)
    delete_session(id)
    return {"id": id}


def _message_items(rows) -> list[dict]:
    return [
        {
            "id": row["id"],
            "role": row["role"],
            "content": row["content"],
            "created_at": row["created_at"],
            "interrupted": bool(row["interrupted"]),
        }
        for row in rows
    ]


@agent_route.get("/sessions/{id}/messages")
def get_session_messages_api(
    id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    around: Optional[int] = Query(None, ge=1),
    before: int = Query(2, ge=0, le=100),
    after: int = Query(3, ge=0, le=100),
):
    user_id, _session = _owned_session(id)
    if around is not None:
        rows, total = list_messages_around(id, user_id, around, before, after)
    else:
        rows, total = list_messages(id, user_id, page, page_size)
    return {"items": _message_items(rows), "total": total}

@agent_route.post("/sessions/search")
def sessions_search_api(query: str = Body(embed=True)):
    """
    跨会话语义搜索。user_id 从 X-User-Id 头（ContextVar）取。
    匿名返回空 items + 固定话术。
    """
    user_id = get_user_id()
    return search_sessions(query,user_id)