from typing import Any, Optional

from xncagent.rdbms.postgres import get_cursor

Row = dict[str, Any]

def create_messages_batch(
    session_id: int,
    user_id: int,
    query: str,
    answer: str,
    scene: str | None = None,
    rewritten_query: str | None = None,
    degraded: bool = False,
    emotion_alert: bool = False,
) -> tuple[int, int]:
    """
    一轮消息一次 INSERT（user + assistant），返回 (user_msg_id, assistant_msg_id)。
    元信息挂在 user 行上（scene/rewritten_query/degraded/emotion_alert 都是对用户输入的判定）。
    """
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO chat_messages
                (session_id, user_id, role, content,
                 scene, rewritten_query, degraded, emotion_alert)
            VALUES
                (%s, %s, 'user',      %s, %s,   %s,   %s,   %s),
                (%s, %s, 'assistant', %s, NULL, NULL, FALSE, FALSE)
            RETURNING id, role
            """,
            (
                session_id, user_id, query, scene, rewritten_query, degraded, emotion_alert,
                session_id, user_id, answer,
            ),
        )
        rows = cur.fetchall()
        ids = {row["role"]: row["id"] for row in rows}

        # 一条 INSERT 插了两行，计数 +2；last_message_at 一起刷
        cur.execute(
            """
            UPDATE chat_sessions
            SET message_count = message_count + 2,
                last_message_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND user_id = %s
            """,
            (session_id, user_id),
        )
        return ids["user"], ids["assistant"]


def update_token_count(message_id: int, token_count: int) -> None:
    """流式结束后回填 assistant 行的 token_count。"""
    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE chat_messages
            SET token_count = %s
            WHERE id = %s
            """,
            (token_count, message_id),
        )


def list_messages(
    session_id: int,
    user_id: Optional[int] = None,
    page: int = 1,
    page_size: int = 30,
) -> tuple[list[Row], int]:
    """
    消息分页：倒序返回，最新一页在前，前端翻历史往上翻。
    返回 (items, total)。
    """
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    offset = (page - 1) * page_size

    where = ["session_id = %s"]
    params: list[Any] = [session_id]

    if user_id is not None:
        where.append("user_id = %s")
        params.append(user_id)

    where_sql = " AND ".join(where)

    with get_cursor() as cur:
        cur.execute(
            f"SELECT COUNT(*) AS cnt FROM chat_messages WHERE {where_sql}",
            params,
        )
        total = cur.fetchone()["cnt"]

        cur.execute(
            f"""
            SELECT id, role, content, created_at
            FROM chat_messages
            WHERE {where_sql}
            ORDER BY created_at DESC, id DESC
            LIMIT %s OFFSET %s
            """,
            (*params, page_size, offset),
        )
        return cur.fetchall(), total


def list_messages_around(
    session_id: int,
    user_id: int,
    around: int,
    before: int = 2,
    after: int = 3,
) -> tuple[list[Row], int]:
    """
    以 around 为锚取窗口。
    id < around 按 id 倒序取 before 条，id >= around 按 id 正序取 after+1 条，合并后按 id 正序。
    total 仍是该会话消息总数。
    """
    before = max(before, 0)
    after = max(after, 0)
    where = "session_id = %s AND user_id = %s"
    scope = (session_id, user_id)
    with get_cursor() as cur:
        cur.execute(
            f"SELECT COUNT(*) AS cnt FROM chat_messages WHERE {where}",
            scope,
        )
        total = cur.fetchone()["cnt"]
        older: list[Row] = []
        if before:
            cur.execute(
                f"""
                SELECT id, role, content, created_at
                FROM chat_messages
                WHERE {where} AND id < %s
                ORDER BY id DESC
                LIMIT %s
                """,
                (*scope, around, before),
            )
            older = list(reversed(cur.fetchall()))
        cur.execute(
            f"""
            SELECT id, role, content, created_at
            FROM chat_messages
            WHERE {where} AND id >= %s
            ORDER BY id ASC
            LIMIT %s
            """,
            (*scope, around, after + 1),
        )
        return older + cur.fetchall(), total


def get_history_by_session(
    session_id: int,
    turns: int = 5,
) -> str:
    """
    取最近 turns 轮历史，拼成：
        主人: ...
        小喜子: ...
    用于替换旧的 get_history_by_user_id。
    """
    limit = max(turns, 0) * 2
    if limit <= 0:
        return ""

    with get_cursor() as cur:
        cur.execute(
            f"""
            SELECT role, content
            FROM (
                SELECT role, content, created_at, id
                FROM chat_messages
                WHERE session_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
            ) AS recent
            ORDER BY created_at ASC, id ASC
            """,
            (session_id, limit),
        )
        rows = cur.fetchall()

    lines: list[str] = []
    for row in rows:
        if row["role"] == "user":
            lines.append(f"主人: {row['content']}")
        elif row["role"] == "assistant":
            lines.append(f"小喜子: {row['content']}")
    return "\n".join(lines)


def list_messages_for_summary(
    session_id: int,
    user_id: Optional[int] = None,
) -> tuple[str, int]:
    """
    摘要刷新时用：该会话全部消息，正序。
    拼成「主人: ...\\n小喜子: ...」，返回 (正文, 覆盖到的消息条数)。
    """
    where = ["session_id = %s"]
    params: list[Any] = [session_id]

    if user_id is not None:
        where.append("user_id = %s")
        params.append(user_id)

    where_sql = " AND ".join(where)

    with get_cursor() as cur:
        cur.execute(
            f"""
            SELECT role, content
            FROM chat_messages
            WHERE {where_sql}
            ORDER BY created_at ASC, id ASC
            """,
            params,
        )
        rows = cur.fetchall()

    lines: list[str] = []
    for row in rows:
        if row["role"] == "user":
            lines.append(f"主人: {row['content']}")
        elif row["role"] == "assistant":
            lines.append(f"小喜子: {row['content']}")
    return "\n".join(lines), len(rows)


def get_messages_by_ids(message_ids: list[int]) -> dict[int, Row]:
    """按 message_id 批量取 created_at。"""
    if not message_ids:
        return {}
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, created_at
            FROM chat_messages
            WHERE id = ANY(%s)
            """,
            (list(message_ids),),
        )
        return {row["id"]: row for row in cur.fetchall()}


def get_message_count(
    session_id: int,
    user_id: Optional[int] = None,
) -> int:
    """按消息表实时计数；摘要触发也可以直接用 chat_sessions.message_count。"""
    where = ["session_id = %s"]
    params: list[Any] = [session_id]

    if user_id is not None:
        where.append("user_id = %s")
        params.append(user_id)

    where_sql = " AND ".join(where)

    with get_cursor() as cur:
        cur.execute(
            f"SELECT COUNT(*) AS cnt FROM chat_messages WHERE {where_sql}",
            params,
        )
        return cur.fetchone()["cnt"]
