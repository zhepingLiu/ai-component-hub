from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI
import httpx

from .health import router as health_router
from .redis_client import create_redis_client
from .agent_registry import build_gateway_entries, load_agent_configs
from .logging_utils import setup_logging
from .middleware import RequestLogMiddleware
from .config import settings
from .services.inmemory_queue import InMemoryJobQueue
from .services.staging_cleanup import run_staging_cleanup_loop

from .routers.agent_gateway import router as agent_gateway_router
from .routers.agent_runner import router as agent_runner_router
from .routers.batches import router as batches_router
from .batch.definitions import build_batch_registry

setup_logging(
    service_name="orchestrator",
    log_dir=settings.LOG_DIR,
    level=settings.LOG_LEVEL,
    retention_days=settings.LOG_RETENTION_DAYS,
    system_code=settings.SYSTEM_CODE,
    max_bytes=settings.LOG_MAX_BYTES,
)
logger = logging.getLogger("orchestrator")

async def register_to_gateway():
    agent_configs = load_agent_configs(settings.AGENT_CONFIG_FILE)
    endpoints = build_gateway_entries(
        agents=agent_configs,
        base_url=settings.ORCHESTRATOR_BASE_URL,
        category=settings.GATEWAY_CATEGORY,
    )
    endpoints.append(
        {
            "category": settings.BATCH_GATEWAY_CATEGORY,
            "action": settings.BATCH_GATEWAY_ACTION,
            "url": f"{settings.ORCHESTRATOR_BASE_URL.rstrip('/')}/batches",
        }
    )
    headers = {"X-Api-Key": settings.GW_API_KEY} if settings.GW_API_KEY else {}

    async with httpx.AsyncClient() as client:
        for ep in endpoints:
            ok = False
            for attempt in range(1, settings.REGISTER_MAX_ATTEMPTS + 1):
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
                await asyncio.sleep(settings.REGISTER_RETRY_SECONDS)
            if not ok:
                logger.error({"event": "gateway.register.giveup", "action": ep["action"]})

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


def _build_agent_queues(agent_configs: dict[str, dict]) -> dict[str, InMemoryJobQueue]:
    queues: dict[str, InMemoryJobQueue] = {}
    for name, cfg in agent_configs.items():
        queue_size = cfg.get("queue_size")
        consumer_count = cfg.get("consumer_count") or cfg.get("consumers")
        if name == "doc-ocr":
            queue_size = queue_size or settings.DOC_OCR_QUEUE_SIZE
            consumer_count = consumer_count or settings.DOC_OCR_CONSUMERS
        queues[name] = InMemoryJobQueue(
            maxsize=int(queue_size or settings.DOC_OCR_QUEUE_SIZE),
            consumer_count=int(consumer_count or settings.DOC_OCR_CONSUMERS),
        )
    return queues


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = None
    app.state.redis_lock = asyncio.Lock()
    app.state.agent_configs = {}
    app.state.agent_queues = {}
    app.state.staging_cleanup_task = None
    app.state.staging_cleanup_stop_event = asyncio.Event()
    app.state.batch_registry = build_batch_registry()

    app.state.agent_configs = load_agent_configs(settings.AGENT_CONFIG_FILE)
    if app.state.agent_configs:
        logger.info({"event": "agents.config_loaded", "count": len(app.state.agent_configs)})
    app.state.agent_queues = _build_agent_queues(app.state.agent_configs)
    app.state.doc_ocr_queue = app.state.agent_queues.get("doc-ocr")
    for name, queue in app.state.agent_queues.items():
        logger.info(
            {
                "event": "agent.queue.configured",
                "agent": name,
                "consumer_count": queue.consumer_count,
                "queue_size": queue.maxsize,
            }
        )
        await queue.start()

    await register_to_gateway()

    if settings.STAGING_CLEANUP_ENABLED:
        app.state.staging_cleanup_task = asyncio.create_task(
            run_staging_cleanup_loop(
                staging_dir=settings.STAGING_DIR,
                retention_sec=settings.STAGING_RETENTION_SEC,
                interval_sec=settings.STAGING_CLEANUP_INTERVAL_SEC,
                stop_event=app.state.staging_cleanup_stop_event,
            )
        )
        logger.info(
            {
                "event": "staging.cleanup_started",
                "staging_dir": settings.STAGING_DIR,
                "retention_sec": settings.STAGING_RETENTION_SEC,
                "interval_sec": settings.STAGING_CLEANUP_INTERVAL_SEC,
            }
        )
    else:
        logger.info({"event": "staging.cleanup_disabled"})

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
            app.state.staging_cleanup_stop_event.set()
            if app.state.staging_cleanup_task:
                await app.state.staging_cleanup_task
        except Exception:
            pass
        try:
            for queue in app.state.agent_queues.values():
                await queue.stop()
        except Exception:
            pass
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
    app.add_middleware(RequestLogMiddleware)

    app.include_router(health_router, tags=["health"])
    app.include_router(agent_gateway_router, tags=["agents"])
    app.include_router(agent_runner_router, tags=["agents"])
    app.include_router(batches_router, tags=["batches"])

    return app


app = create_app()
