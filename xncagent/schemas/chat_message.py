from datetime import datetime
from pydantic import BaseModel


class ChatMessage(BaseModel):
    id: int
    session_id: int
    user_id: int
    role: str                      # "user" / "assistant"
    content: str
    scene: str | None = None
    rewritten_query: str | None = None
    degraded: bool = False
    emotion_alert: bool = False
    token_count: int | None = None
    created_at: datetime


class ChatMessageCreate(BaseModel):
    session_id: int
    user_id: int
    role: str
    content: str
    scene: str | None = None
    rewritten_query: str | None = None
    degraded: bool = False
    emotion_alert: bool = False
    # token_count 不在创建时传，流式结束后单独 UPDATE 回填