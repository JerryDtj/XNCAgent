from fastapi import Request
from fastapi.responses import JSONResponse
import uuid
from xncagent.utils.context import set_request_id

async def request_id_middleware(request: Request, call_next) -> JSONResponse:
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    set_request_id(request_id)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response