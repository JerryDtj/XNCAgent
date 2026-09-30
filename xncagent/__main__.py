from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from xncagent.request_test import router as test_router
from xncagent.route.agent_route import agent_route
from xncagent.utils.exception_handle import register_exception_handler
from xncagent.utils.logger import suppress_noisy_access_logs
from xncagent.utils.middleware import request_id_middleware
from xncagent.rdbms.postgres import init_pool, close_pool

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_pool()
    yield
    close_pool()


app = FastAPI(title="小奴才聊天系统", version="0.1.0", lifespan=lifespan)
app.include_router(test_router)
app.include_router(agent_route)
register_exception_handler(app)
app.middleware("http")(request_id_middleware)
suppress_noisy_access_logs()


@app.get("/")
async def root():
    return {"service": "小奴才聊天系统", "version": app.version, "docs": "/docs"}


@app.get("/health")
async def health():
    return {"status": "ok"}


def main() -> None:
    # 8000 落在本机 TCP 动态端口区间 1024–15000，会被其他进程占用，bind 返回 WinError 10013。
    uvicorn.run("xncagent.__main__:app", host="0.0.0.0", port=18000, reload=True)


if __name__ == "__main__":
    main()
