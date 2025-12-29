from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi import FastAPI

from .health import router as health_router
from .redis_client import create_redis_client

from .routers.document_ocr import router as doc_ocr_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 初始化 Redis（无状态服务的外部依赖）
    r = create_redis_client()

    # 强制连通性检查：如果 Redis 不可用，直接让服务启动失败
    # 这样上线时不会出现“跑起来了但执行不了”的半死状态
    r.ping()

    app.state.redis = r
    try:
        yield
    finally:
        # redis-py 没有显式 close 也可，但这里做得更干净
        try:
            r.close()
        except Exception:
            pass


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Component Hub - Orchestrator",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(health_router, tags=["health"])

    return app


app = create_app()
app.include_router(doc_ocr_router, prefix="/agents", tags=["agents"])