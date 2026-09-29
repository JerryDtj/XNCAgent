import uvicorn
from fastapi import FastAPI

from xncagent.request_test import router as test_router
from xncagent.route.agent_route import agent_route
from xncagent.utils.exception_handle import register_exception_handler
from xncagent.utils.middleware import request_id_middleware
from xncagent.rdbms.postgres import init_pool, close_pool

app = FastAPI(title="小奴才聊天系统", version="0.1.0")
app.include_router(test_router)
app.include_router(agent_route)
register_exception_handler(app)
app.middleware("http")(request_id_middleware)



@app.get("/health")
async def health():
    return {"status": "ok"}


def main() -> None:
    uvicorn.run("xncagent.__main__:app", host="0.0.0.0", port=8000, reload=True)
    init_pool()
    close_pool()


if __name__ == "__main__":
    main()
