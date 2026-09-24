from fastapi import Request
from xncagent.utils.logger import logger
from fastapi.responses import JSONResponse
from fastapi import FastAPI
from xncagent.utils.response import fail
from typing import Optional
from exceptions import NotFoundException, BadRequestException, UnauthorizedException, BizException


async def hand_not_found_exception(exc: NotFoundException,request: Optional[Request] = None):
    logger.error(f"资源未找到: {exc.message}")
    return fail(NotFoundException.code, message=exc.message, request=request)

async def hand_bad_request_exception(exc: BadRequestException,request: Optional[Request] = None):
    logger.error(f"请求参数错误: {request.url} {exc.message}")
    return fail(http_code=400, code=BadRequestException.code, message=exc.message, request=request)

async def hand_unauthorized_exception(exc: UnauthorizedException,request: Optional[Request] = None):
    logger.error(f"未授权: {request.url} {exc.message}")
    return fail(http_code=401, code=UnauthorizedException.code, message=exc.message, request=request)

async def hand_biz_exception(exc: BizException,request: Optional[Request] = None):
    logger.error(f"业务处理失败: {exc.message}")
    return fail(http_code=509, code=BizException.code, message=exc.message, request=request)

def register_exception_handler(app: FastAPI):
    app.add_exception_handler(NotFoundException, hand_not_found_exception)
    app.add_exception_handler(BadRequestException, hand_bad_request_exception)
    app.add_exception_handler(UnauthorizedException, hand_unauthorized_exception)
    app.add_exception_handler(BizException, hand_biz_exception)