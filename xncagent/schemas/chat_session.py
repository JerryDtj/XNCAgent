from datetime import datetime
from pydantic import BaseModel, Field


class ChatSession(BaseModel):
    """对应 chat_sessions 表一行"""
    id: int
    user_id: int
    title: str = ""
    status: str = "active"
    message_count: int = 0
    last_message_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ChatSessionCreate(BaseModel):
    """新建会话时的入参（其实只有 user_id）"""
    user_id: int
    title: str = Field("", max_length=100)