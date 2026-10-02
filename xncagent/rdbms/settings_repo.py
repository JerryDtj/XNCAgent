"""用户设置仓储：user_settings 表读写。"""

from typing import Any

from xncagent.rdbms.postgres import get_cursor

Row = dict[str, Any]

_DEFAULT_MUSIC_ENABLED = True


def get_settings(user_id: int) -> Row:
    """
    读用户设置。无行则 INSERT 默认值后返回（upsert 读取）。
    查询/写入失败向上抛异常，由上层决定兜底。
    """
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_settings (user_id, music_enabled)
            VALUES (%s, %s)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (user_id, _DEFAULT_MUSIC_ENABLED),
        )
        cur.execute(
            """
            SELECT user_id, music_enabled, updated_at
            FROM user_settings
            WHERE user_id = %s
            """,
            (user_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"user_settings upsert 后仍无行: user_id={user_id}")
        return row


def update_settings(user_id: int, music_enabled: bool) -> Row:
    """
    更新 music_enabled（upsert）。失败向上抛异常。
    user_id: 用户ID
    music_enabled: 音乐是否启用
    返回: 更新后的用户设置
    """
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_settings (user_id, music_enabled, updated_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id) DO UPDATE
            SET music_enabled = EXCLUDED.music_enabled,
                updated_at = CURRENT_TIMESTAMP
            RETURNING user_id, music_enabled, updated_at
            """,
            (user_id, music_enabled),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"user_settings upsert 未返回行: user_id={user_id}")
        return row
