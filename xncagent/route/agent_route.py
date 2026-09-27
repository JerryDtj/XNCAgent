from fastapi import APIRouter, Body
from fastapi.responses import StreamingResponse

from xncagent.agent.xiaoxizi_agent import task, task_stream


agent_route = APIRouter(prefix="/agent", tags=["agent"])

@agent_route.post("/chat")
async def chat(message: str = Body(embed=True)):
    return await task(message)

@agent_route.post("/chat/stream")
async def chat_stream(message: str = Body(embed=True)):
    return StreamingResponse(
        task_stream(message),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )