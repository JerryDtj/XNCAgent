from datetime import datetime
from pydantic import BaseModel


class ChatSessionSummary(BaseModel):
    session_id: int
    user_id: int
    summary: str = ""
    message_count: int = 0
    updated_at: datetime