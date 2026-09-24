from cli import main
from xncagent.utils.exception_handle import register_exception_handler
from xncagent.utils.middleware import request_id_middleware
from fastapi import FastAPI 
from xncagent.route.agent_route import agent_router


if __name__ == "__main__":
    app = FastAPI(title="小奴才聊天系统", version="0.1.0")
    app.add_route(agent_router,prefix="/api/v1/agent")
    register_exception_handler(app)
    app.add_middleware(middleware_class=request_id_middleware)
    main(app)

@app.get("/health")
async def health():
    return {"status": "ok"}