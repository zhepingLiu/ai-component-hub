import asyncio
import logging
import os

import httpx

from .schemas import AddReq, StdResp
from contextlib import asynccontextmanager
from fastapi import FastAPI
from .logging_utils import setup_logging

LOG_DIR = os.getenv("LOG_DIR", "/app/data/logs")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "10"))

setup_logging("tools-basic", LOG_DIR, LOG_LEVEL, LOG_RETENTION_DAYS)
logger = logging.getLogger("tools-basic")

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://gateway:8000")
GW_API_KEY = os.getenv("GW_API_KEY")

REGISTER_RETRY_SECONDS = 2
REGISTER_MAX_ATTEMPTS = 15

async def register_to_gateway():
    endpoints = [
        {"category": "tools", "action": "echo", "url": "http://tools-basic:7001/echo"},
        {"category": "tools", "action": "add", "url": "http://tools-basic:7001/add"},
    ]

    headers = {"X-Api-Key": GW_API_KEY} if GW_API_KEY else {}

    async with httpx.AsyncClient() as client:
        for ep in endpoints:
            ok = False
            for attempt in range(1, REGISTER_MAX_ATTEMPTS + 1):
                try:
                    resp = await client.post(f"{GATEWAY_URL}/register", json=ep, headers=headers, timeout=5)
                    if resp.status_code == 200:
                        ok = True
                        logger.info(
                            {"event": "gateway.registered", "action": ep["action"], "status": resp.status_code}
                        )
                        break
                    logger.warning(
                        {
                            "event": "gateway.register.failed",
                            "action": ep["action"],
                            "status": resp.status_code,
                            "attempt": attempt,
                        }
                    )
                except Exception as e:
                    logger.warning(
                        {
                            "event": "gateway.register.error",
                            "action": ep["action"],
                            "attempt": attempt,
                            "error": str(e),
                        }
                    )
                await asyncio.sleep(REGISTER_RETRY_SECONDS)
            if not ok:
                logger.error({"event": "gateway.register.giveup", "action": ep["action"]})


@asynccontextmanager
async def lifespan(app: FastAPI):
    await register_to_gateway()
    yield

app = FastAPI(title="tools-basic", lifespan=lifespan)

@app.get("/health")
def health():
    return "ok"

@app.get("/echo")
def echo(q: str = "hello"):
    logger.info({"event": "tools.echo", "q": q})
    return StdResp(data={"echo": q})

@app.post("/add")
def add(req: AddReq):
    logger.info({"event": "tools.add", "a": req.a, "b": req.b})
    return StdResp(data={"sum": req.a + req.b})
