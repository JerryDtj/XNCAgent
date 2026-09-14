from fastapi import FastAPI
from xncagent.request_test import router as test_router

app = FastAPI()
app.include_router(test_router)

@app.get("/")
async def root():
    return {"message": "Hello, World!"}
