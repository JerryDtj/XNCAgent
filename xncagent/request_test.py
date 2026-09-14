from fastapi import APIRouter, Request

router = APIRouter()

@router.get("/test")
async def root(request: Request):
    return {"message": "Hello, World! ", "request": str(request.method)}
