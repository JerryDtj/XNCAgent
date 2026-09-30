from fastapi import Request
from fastapi.responses import JSONResponse
import uuid
from xncagent.utils.context import set_request_id, set_user_id
from xncagent.utils.logger import logger

def _to_pos_int_or_none(value):
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        logger.warning(f"userId验证失败: {value}")
        return None
    if n <= 0:
        logger.warning(f"userId验证失败: {value}")
        return None
    return n

async def request_id_middleware(request: Request, call_next) -> JSONResponse:
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    set_request_id(request_id)
    set_user_id(_to_pos_int_or_none(request.headers.get("X-User-Id")))
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response