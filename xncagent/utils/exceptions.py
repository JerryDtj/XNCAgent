class AgentException(Exception):
    code: int = 500
    message: str = "服务器内部错误"

    def __init__(self, message: str | None = None):
        if message is not None:
            self.message = message
        super().__init__(self.message)


class NotFoundException(AgentException):
    code: int = 404
    message: str = "资源未找到"

class BadRequestException(AgentException):
    code: int = 501
    message: str = "请求参数错误"

class BizException(AgentException):
    code: int = 509
    message: str = "业务处理失败"

class UnauthorizedException(AgentException):
    code: int = 401
    message: str = "未授权"