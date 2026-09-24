from typing import Optional
from contextvars import ContextVar

_request_id_context: ContextVar[Optional[str]] = ContextVar("request_id", default=None)

def set_request_id(request_id: Optional[str]) -> None:
    _request_id_context.set(request_id)

def get_request_id() -> Optional[str]:
    return _request_id_context.get()