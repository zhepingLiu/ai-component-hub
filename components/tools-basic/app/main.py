import asyncio
import httpx
import os

from .schemas import AddReq, StdResp
from contextlib import asynccontextmanager
from fastapi import FastAPI

app = FastAPI(title="tools-basic")

@app.get("/health")
def health():
    return "ok"

@app.get("/echo")
def echo(q: str = "hello"):
    return StdResp(data={"echo": q})

@app.post("/add")
def add(req: AddReq):
    return StdResp(data={"sum": req.a + req.b})

# TODO: 自动注册的代码,目前有bug
# async def register_to_gateway():
#     gateway_url = os.getenv("GATEWAY_URL", "http://gateway:8000")
#     api_key = os.getenv("GW_API_KEY")
    
#     endpoints = [
#         {"category": "tools", "action": "echo", "url": "http://tools-basic:7001/echo"},
#         {"category": "tools", "action": "add", "url": "http://tools-basic:7001/add"},
#     ]
    
#     if api_key:
#         headers = {"X-Api-Key": api_key}
#     else:
#         headers = {}
    
#     async with httpx.AsyncClient() as client:
#         for ep in endpoints:
#             try:
#                 resp = await client.post(f"{gateway_url}/register", json=ep, headers=headers)
#                 print(f"[register] {ep['action']} -> {resp.status_code}")
#             except Exception as e:
#                 print(f"[register failed] {ep['action']}: {e}")

# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     # 应用启动阶段
#     await asyncio.sleep(3)  # 等待 gateway 启动
#     await register_to_gateway()
#     yield
#     # 应用关闭阶段（可选）
#     # await unregister_from_gateway()

# app = FastAPI(title="tools-basic", lifespan=lifespan)
    