from typing import Any, Optional

from xncagent.rdbms.postgres import get_cursor
from xncagent.schemas.chat_session import ChatSession

Row = dict[str, Any]


def create_session(user_id:int, title:str = "") -> int:
    """新建会话，返回 session_id。"""
    with get_cursor() as cur:
        cur.execute("""
            INSERT INTO chat_sessions (user_id, title)
            VALUES (%s, %s)
            RETURNING id
        """, (user_id, title))
        return cur.fetchone()["id"]

def get_session(session_id:int, user_id:Optional[int]) -> Optional[ChatSession]:
    """按 id + user_id 精确匹配。user_id 为空直接返回 None，不查库。"""
    if user_id is None:
        return None
    with get_cursor() as cur:
        cur.execute("""
            SELECT id, user_id, title, message_count, last_message_at, created_at, updated_at
            FROM chat_sessions
            WHERE id = %s AND user_id = %s
        """, (session_id, user_id))
        row = cur.fetchone()
        return ChatSession(**row) if row else None

def update_session_title_if_empty(session_id: int, title: str) -> bool:
    """只在该会话 title 仍为空时更新，返回是否更新成功。"""
    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE chat_sessions
            SET title = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND title = ''
            """,
            (title, session_id),
        )
        return cur.rowcount > 0

def update_session_title(session_id: int, title: str) -> bool:
    """用户改名：无条件覆盖 title。"""
    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE chat_sessions
            SET title = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (title, session_id),
        )
        return cur.rowcount > 0

def delete_session(session_id:int) -> None:
    with get_cursor() as cur:
        cur.execute("""
            DELETE FROM chat_sessions
            WHERE id = %s
        """, (session_id,))

def list_sessions(
    user_id: int,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Row], int]:
    """按 last_message_at 倒序分页；LEFT JOIN 摘要，没有则 summary 为空串。"""
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    offset = (page - 1) * page_size
    with get_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM chat_sessions WHERE user_id = %s",
            (user_id,),
        )
        total = cur.fetchone()["cnt"]
        cur.execute(
            """
            SELECT
                s.id,
                s.title,
                COALESCE(ss.summary, '') AS summary,
                s.message_count,
                s.last_message_at
            FROM chat_sessions s
            LEFT JOIN chat_session_summaries ss ON ss.session_id = s.id
            WHERE s.user_id = %s
            ORDER BY s.last_message_at DESC NULLS LAST, s.id DESC
            LIMIT %s OFFSET %s
            """,
            (user_id, page_size, offset),
        )
        return cur.fetchall(), total

def get_session_message_count(session_id:int) -> int:
    with get_cursor() as cur:
        cur.execute("""
            SELECT message_count
            FROM chat_sessions
            WHERE id = %s
        """, (session_id,))
        return cur.fetchone()["message_count"]

def update_session_message_count(session_id:int, message_count:int) -> None:
    with get_cursor() as cur:
        cur.execute("""
            UPDATE chat_sessions
            SET message_count = %s
            WHERE id = %s
        """, (message_count, session_id))

def get_sessions_by_ids(session_ids: list[int]) -> dict[int, Row]:
    """按 session_id 批量取 title / created_at。"""
    if not session_ids:
        return {}
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, title, created_at
            FROM chat_sessions
            WHERE id = ANY(%s)
            """,
            (list(session_ids),),
        )
        return {row["id"]: row for row in cur.fetchall()}

def delete_session_messages(session_id:int) -> None:
    with get_cursor() as cur:
        cur.execute("DELETE FROM chat_messages WHERE session_id = %s", (session_id,))

def get_summary_covered_count(session_id: int) -> int:
    """摘要已覆盖的消息数。没有摘要行时视为 0（全部未覆盖）。"""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT message_count
            FROM chat_session_summaries
            WHERE session_id = %s
            """,
            (session_id,),
        )
        row = cur.fetchone()
        return row["message_count"] if row else 0

def upsert_session_summary(
    session_id: int,
    user_id: int,
    summary: str,
    message_count: int,
) -> None:
    """一会话一条。冲突时更新摘要正文、覆盖条数和刷新时间。"""
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO chat_session_summaries
                (session_id, user_id, summary, message_count, updated_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (session_id) DO UPDATE
            SET summary = EXCLUDED.summary,
                message_count = EXCLUDED.message_count,
                updated_at = CURRENT_TIMESTAMP
            """,
            (session_id, user_id, summary, message_count),
        )
