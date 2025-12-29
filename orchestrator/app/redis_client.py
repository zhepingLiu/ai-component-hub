from __future__ import annotations

from typing import Optional

import redis
from redis import Redis

from .config import settings


def create_redis_client() -> Redis:
    return redis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD,
        decode_responses=True,   # 统一用 str
        socket_connect_timeout=2,
        socket_timeout=5,
    )


def get_redis(app) -> Redis:
    r: Optional[Redis] = getattr(app.state, "redis", None)
    if r is None:
        raise RuntimeError("Redis client is not initialized on app.state.redis")
    return r
