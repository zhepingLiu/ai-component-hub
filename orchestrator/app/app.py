from __future__ import annotations

from fastapi import FastAPI

# 先只引入 health，确保最小可运行
from .health import router as health_router


def create_app() -> FastAPI:
    """
    Orchestrator FastAPI application factory.

    设计原则：
    - app.py 只负责装配（middleware、router、生命周期管理等）
    - 业务逻辑放到 routers/ + services/
    """
    app = FastAPI(
        title="AI Component Hub - Orchestrator",
        version="0.1.0",
    )

    # ---- Routers ----
    app.include_router(health_router, tags=["health"])

    # 之后你新增 document_ocr router 时，再在这里 include：
    # from .routers.document_ocr import router as doc_ocr_router
    # app.include_router(doc_ocr_router, prefix="/agents", tags=["agents"])

    return app


# 供 uvicorn/gunicorn 直接引用
app = create_app()
