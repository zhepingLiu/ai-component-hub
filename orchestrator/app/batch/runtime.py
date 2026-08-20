from __future__ import annotations

import asyncio
import logging

from ..config import settings
from ..redis_client import create_redis_client
from .store import RedisBatchStore


async def create_store_when_ready(logger: logging.Logger) -> RedisBatchStore:
    while True:
        try:
            redis_client = create_redis_client()
            await asyncio.to_thread(redis_client.ping)
            logger.info({"event": "batch.redis.ready"})
            return RedisBatchStore(
                redis_client,
                key_prefix=settings.BATCH_REDIS_KEY_PREFIX,
                retention_seconds=settings.BATCH_RETENTION_SEC,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning({"event": "batch.redis.waiting", "error": str(exc)})
            await asyncio.sleep(settings.BATCH_REDIS_RETRY_SEC)
