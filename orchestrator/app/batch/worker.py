from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid

from ..config import settings
from ..logging_utils import setup_logging
from .definition import BatchContext
from .registry import BatchDefinitionRegistry
from .results import TaskExecutionResult
from .store import RedisBatchStore
from .runtime import create_store_when_ready


logger = logging.getLogger("orchestrator.batch.worker")


class BatchWorker:
    def __init__(self, store: RedisBatchStore, registry: BatchDefinitionRegistry, *, worker_id: str | None = None):
        self.store = store
        self.registry = registry
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

    async def run_forever(self) -> None:
        logger.info({"event": "batch.worker.started", "worker_id": self.worker_id})
        last_maintenance = 0.0
        loop = asyncio.get_running_loop()
        while True:
            now = loop.time()
            if now - last_maintenance >= settings.BATCH_MAINTENANCE_INTERVAL_SEC:
                await self.store.promote_due_retries()
                await self.store.recover_expired_leases()
                last_maintenance = now
            task = await self.store.claim_task(self.worker_id, timeout=1)
            if not task:
                continue
            await self._execute(task.task_id)

    async def _execute(self, task_id: str) -> None:
        task = await self.store.get_task(task_id)
        if not task:
            return
        batch = await self.store.get_batch(task.batch_id)
        if not batch:
            await self.store.complete_task(
                task_id,
                TaskExecutionResult.permanent_failure("BATCH_NOT_FOUND", "parent batch is missing"),
            )
            return
        definition = self.registry.get(batch.batch_type, batch.definition_version)
        ctx = BatchContext(batch=batch, settings=settings, logger=logger)
        try:
            result = await asyncio.wait_for(
                definition.execute_task(ctx, task, batch.runtime_context),
                timeout=batch.options.task_timeout_seconds,
            )
        except TimeoutError:
            result = TaskExecutionResult.retryable_failure("TASK_TIMEOUT", "task execution timed out")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception({"event": "batch.task.unhandled", "task_id": task_id, "error": str(exc)})
            result = TaskExecutionResult.retryable_failure("UNHANDLED_EXCEPTION", str(exc))
        await self.store.complete_task(task_id, result)


async def main() -> None:
    from .definitions import build_batch_registry

    setup_logging(
        service_name="batch-worker",
        log_dir=settings.LOG_DIR,
        level=settings.LOG_LEVEL,
        retention_days=settings.LOG_RETENTION_DAYS,
        system_code=settings.SYSTEM_CODE,
        max_bytes=settings.LOG_MAX_BYTES,
    )
    store = await create_store_when_ready(logger)
    registry = build_batch_registry()
    workers = [BatchWorker(store, registry) for _ in range(settings.BATCH_WORKER_CONCURRENCY)]
    await asyncio.gather(*(worker.run_forever() for worker in workers))


if __name__ == "__main__":
    asyncio.run(main())
