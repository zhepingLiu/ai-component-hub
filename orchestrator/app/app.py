from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI

from .health import router as health_router
from .redis_client import create_redis_client
from .agent_registry import load_agent_configs
from .logging_utils import setup_logging
from .config import settings

from .routers.document_ocr import router as doc_ocr_router
from .routers.agent_gateway import router as agent_gateway_router

setup_logging(
    service_name="orchestrator",
    log_dir=settings.LOG_DIR,
    level=settings.LOG_LEVEL,
    retention_days=settings.LOG_RETENTION_DAYS,
)
logger = logging.getLogger("orchestrator")

async def _init_redis_once(app: FastAPI, timeout_sec: float = 2.0) -> bool:
    if app.state.redis:
        return True
    async with app.state.redis_lock:
        if app.state.redis:
            return True
        try:
            r = await asyncio.wait_for(
                asyncio.to_thread(create_redis_client),
                timeout=timeout_sec,
            )
            await asyncio.wait_for(asyncio.to_thread(r.ping), timeout=timeout_sec)
            app.state.redis = r
            logger.info({"event": "redis.ping.ok"})
            return True
        except asyncio.TimeoutError:
            logger.warning({"event": "redis.init_timeout", "timeout_sec": timeout_sec})
            app.state.redis = None
            return False
        except Exception as exc:
            logger.exception({"event": "redis.init_failed", "error": str(exc)})
            app.state.redis = None
            return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = None
    app.state.redis_lock = asyncio.Lock()
    app.state.agent_configs = {}

    app.state.agent_configs = load_agent_configs(settings.AGENT_CONFIG_FILE)
    if app.state.agent_configs:
        logger.info({"event": "agents.config_loaded", "count": len(app.state.agent_configs)})

    if settings.REDIS_REQUIRED:
        # 后台初始化，避免启动被 Redis 阻塞
        asyncio.create_task(_init_redis_once(app))
    else:
        logger.warning({"event": "redis.disabled"})
    try:
        yield
    finally:
        # redis-py 没有显式 close 也可，但这里做得更干净
        try:
            if app.state.redis:
                app.state.redis.close()
        except Exception:
            pass


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Component Hub - Orchestrator",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(health_router, tags=["health"])
    app.include_router(agent_gateway_router, tags=["agents"])

    return app


app = create_app()
app.include_router(doc_ocr_router, prefix="/agents", tags=["agents"])
