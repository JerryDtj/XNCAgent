from fastapi import APIRouter, Request
from xncagent.agent.xiaoxizi_agent import task_check


agent_route = APIRouter(prefix="/agent", tags=["agent"])

@agent_route.post("/chat")
async def chat(request: Request, prompt: str):
    return await task_check(prompt)