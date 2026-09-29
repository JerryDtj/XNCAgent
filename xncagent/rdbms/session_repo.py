from sqlite3 import Row
from xncagent.rdbms.postgres import get_cursor
from xncagent.schemas.chat_session import ChatSession
from typing import Optional

def create_session(user_id:int, title:str = "") -> int:
    """新建会话，返回 session_id。"""
    with get_cursor() as cur:
        cur.execute("""
            INSERT INTO chat_sessions (user_id, title)
            VALUES (%s, %s)
            RETURNING id
        """, (user_id, title))
        return cur.fetchone()[0]

def get_session(session_id:int, user_id:Optional[int]) -> Optional[ChatSession]:
    """按 id + user_id 查会话，用于越权校验；不存在返回 None。"""
    with get_cursor() as cur:
        cur.execute("""
            SELECT id, user_id, title, message_count, last_message_at, created_at, updated_at
            FROM chat_sessions
            WHERE id = %s AND user_id = COALESCE(%s, user_id)
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

def delete_session(session_id:int) -> None:
    with get_cursor() as cur:
        cur.execute("""
            DELETE FROM chat_sessions
            WHERE id = %s
        """, (session_id,))

def list_sessions(user_id:int) -> List[ChatSession]:
    with get_cursor() as cur:
        cur.execute("""
            SELECT id, user_id, title, message_count, last_message_at, created_at, updated_at
            FROM chat_sessions
            WHERE user_id = %s
        """, (user_id,))
        return [ChatSession(**row) for row in cur.fetchall()]

def get_session_message_count(session_id:int) -> int:
    with get_cursor() as cur:
        cur.execute("""
            SELECT message_count
            FROM chat_sessions
            WHERE id = %s
        """, (session_id,))
        return cur.fetchone()[0]

def update_session_message_count(session_id:int, message_count:int) -> None:
    with get_cursor() as cur:
        cur.execute("""
            UPDATE chat_sessions
            SET message_count = %s
            WHERE id = %s
        """, (message_count, session_id))

def delete_session_messages(session_id:int) -> None:
    with get_cursor() as cur:
        cur.execute("DELETE FROM chat_messages WHERE session_id = %s", (session_id,))
