from typing import Any, Optional, TypeVar
from fastapi import Request
from pydantic import BaseModel
from typing import Generic
from fastapi.responses import JSONResponse

from xncagent.utils.context import get_request_id



T = TypeVar('T')
class Body(BaseModel,Generic[T]):
    code: int = 200
    message: str = "success"
    data: Optional[T] = None
    request_id: Optional[str] = None

def _rid(request: Optional[Request] = None) -> Optional[str]:
    if request is not None:
        return request.headers.get("X-Request-ID")
    return get_request_id()

def _write(http_code, code: int, message: str, data: Any = None, request: Optional[Request] = None) -> JSONResponse:
    body = Body(code=code, message=message, data=data, request_id=_rid(request))
    return JSONResponse(status_code=http_code, content=body.model_dump())

def success(data: Any = None, request: Optional[Request] = None) -> JSONResponse:
    return _write(200, 200, "success", data, request)

def fail( code: int, http_code: int = 500, message: str = "error", data: Any = None, request: Optional[Request] = None) -> JSONResponse:
    return _write(http_code, code, message, data, request)
