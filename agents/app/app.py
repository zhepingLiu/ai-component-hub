from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import logging

import httpx
from fastapi import FastAPI

from .health import router as health_router
from .redis_client import create_redis_client
from .logging_utils import setup_logging
from .config import settings

from .routers.document_ocr import router as doc_ocr_router

setup_logging(
    service_name="agents",
    log_dir=settings.LOG_DIR,
    level=settings.LOG_LEVEL,
    retention_days=settings.LOG_RETENTION_DAYS,
)
logger = logging.getLogger("agents")

REGISTER_RETRY_SECONDS = 2
REGISTER_MAX_ATTEMPTS = 15


async def register_to_gateway() -> None:
    if not settings.GATEWAY_URL:
        return

    endpoint_url = settings.AGENTS_SERVICE_URL.rstrip("/") + "/agents/doc-ocr/run"
    endpoints = [
        {"category": "agents", "action": "doc-ocr", "url": endpoint_url},
    ]

    headers = {"X-Api-Key": settings.GW_API_KEY} if settings.GW_API_KEY else {}

    async with httpx.AsyncClient() as client:
        for ep in endpoints:
            ok = False
            for attempt in range(1, REGISTER_MAX_ATTEMPTS + 1):
                try:
                    resp = await client.post(
                        f"{settings.GATEWAY_URL}/register",
                        json=ep,
                        headers=headers,
                        timeout=5,
                    )
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
    # 初始化 Redis（无状态服务的外部依赖）
    r = create_redis_client()

    # 强制连通性检查：如果 Redis 不可用，直接让服务启动失败
    # 这样上线时不会出现“跑起来了但执行不了”的半死状态
    r.ping()
    logger.info({"event": "redis.ping.ok"})

    app.state.redis = r
    try:
        await register_to_gateway()
        yield
    finally:
        # redis-py 没有显式 close 也可，但这里做得更干净
        try:
            r.close()
        except Exception:
            pass


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Component Hub - Agents",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(health_router, tags=["health"])

    return app


app = create_app()
app.include_router(doc_ocr_router, prefix="/agents", tags=["agents"])
