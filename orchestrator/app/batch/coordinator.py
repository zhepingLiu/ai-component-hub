from __future__ import annotations

import asyncio
import logging

from ..config import settings
from ..logging_utils import setup_logging
from .definition import BatchContext
from .models import BatchStatus
from .registry import BatchDefinitionRegistry
from .store import RedisBatchStore
from .runtime import create_store_when_ready


logger = logging.getLogger("orchestrator.batch.coordinator")


class BatchCoordinator:
    def __init__(
        self,
        store: RedisBatchStore,
        registry: BatchDefinitionRegistry,
        *,
        concurrency: int | None = None,
    ):
        self.store = store
        self.registry = registry
        self.concurrency = max(1, concurrency or settings.BATCH_COORDINATOR_CONCURRENCY)
        self._running_commands: set[asyncio.Task] = set()

    async def run_forever(self) -> None:
        recovered = await self.store.recover_commands()
        logger.info({"event": "batch.coordinator.started"})
        if recovered:
            logger.warning({"event": "batch.commands.recovered", "count": recovered})
        schedule_task = asyncio.create_task(self._schedule_loop())
        try:
            while True:
                if len(self._running_commands) >= self.concurrency:
                    await asyncio.wait(
                        self._running_commands,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    continue
                command = await self.store.dequeue_command(timeout=1)
                if not command:
                    continue
                task = asyncio.create_task(self._handle_command(command))
                self._running_commands.add(task)
                task.add_done_callback(self._running_commands.discard)
        finally:
            schedule_task.cancel()
            for task in self._running_commands:
                task.cancel()
            await asyncio.gather(schedule_task, *self._running_commands, return_exceptions=True)

    async def _schedule_loop(self) -> None:
        while True:
            try:
                await self.store.promote_due_batches()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception({"event": "batch.schedule.scan_failed", "error": str(exc)})
            await asyncio.sleep(settings.BATCH_MAINTENANCE_INTERVAL_SEC)

    async def _handle_command(self, command: dict[str, str]) -> None:
        acknowledge = True
        try:
            await self.handle(command["action"], command["batch_id"])
        except asyncio.CancelledError:
            acknowledge = False
            raise
        except Exception as exc:
            logger.exception({"event": "batch.command.failed", "command": command, "error": str(exc)})
        finally:
            if acknowledge:
                await self.store.acknowledge_command(command["_raw"])

    async def handle(self, action: str, batch_id: str) -> None:
        if action == "prepare":
            await self._prepare(batch_id)
        elif action == "finalize":
            await self._finalize(batch_id)
        elif action == "cleanup":
            await self._cleanup(batch_id)
        else:
            logger.warning({"event": "batch.command.unknown", "action": action, "batch_id": batch_id})

    async def _prepare(self, batch_id: str) -> None:
        batch = await self.store.get_batch(batch_id)
        if batch and batch.status == BatchStatus.CREATED:
            batch = await self.store.transition_batch(
                batch_id,
                expected={BatchStatus.CREATED},
                target=BatchStatus.PREPARING,
            )
        if not batch:
            return
        if batch.status not in {BatchStatus.PREPARING, BatchStatus.GENERATING_TASKS}:
            return
        definition = self.registry.get(batch.batch_type, batch.definition_version)
        ctx = BatchContext(batch=batch, settings=settings, logger=logger)
        try:
            if batch.status == BatchStatus.PREPARING:
                runtime_context = await definition.prepare(ctx, batch.input)
                await self.store.set_runtime_context(batch_id, runtime_context or {})
                batch = await self.store.transition_batch(
                    batch_id,
                    expected={BatchStatus.PREPARING},
                    target=BatchStatus.GENERATING_TASKS,
                )
                if not batch:
                    return
            else:
                runtime_context = batch.runtime_context
            ctx = BatchContext(batch=batch, settings=settings, logger=logger)
            async for spec in definition.produce_tasks(ctx, batch.input, runtime_context or {}):
                await self.store.create_task(batch_id, spec)
            await self.store.mark_generation_complete(batch_id)
            logger.info({"event": "batch.tasks.generated", "batch_id": batch_id})
        except Exception as exc:
            logger.exception({"event": "batch.prepare.failed", "batch_id": batch_id, "error": str(exc)})
            await self.store.transition_batch(
                batch_id,
                expected={BatchStatus.PREPARING, BatchStatus.GENERATING_TASKS, BatchStatus.RUNNING},
                target=BatchStatus.FAILED,
                error=str(exc),
            )
            await self._cleanup(batch_id)

    async def _finalize(self, batch_id: str) -> None:
        existing = await self.store.get_batch(batch_id)
        batch = existing if existing and existing.status == BatchStatus.FINALIZING else await self.store.begin_finalize(batch_id)
        if not batch:
            return
        definition = self.registry.get(batch.batch_type, batch.definition_version)
        ctx = BatchContext(batch=batch, settings=settings, logger=logger)
        try:
            result = await definition.finalize(ctx)
            await definition.cleanup(ctx)
            await self.store.finish_finalize(batch_id, result=result)
            logger.info({"event": "batch.finalized", "batch_id": batch_id})
        except Exception as exc:
            logger.exception({"event": "batch.finalize.failed", "batch_id": batch_id, "error": str(exc)})
            await self.store.finish_finalize(batch_id, error=str(exc))

    async def _cleanup(self, batch_id: str) -> None:
        batch = await self.store.get_batch(batch_id)
        if not batch:
            return
        definition = self.registry.get(batch.batch_type, batch.definition_version)
        try:
            await definition.cleanup(BatchContext(batch=batch, settings=settings, logger=logger))
        except Exception as exc:
            logger.exception({"event": "batch.cleanup.failed", "batch_id": batch_id, "error": str(exc)})


async def main() -> None:
    from .definitions import build_batch_registry

    setup_logging(
        service_name="batch-coordinator",
        log_dir=settings.LOG_DIR,
        level=settings.LOG_LEVEL,
        retention_days=settings.LOG_RETENTION_DAYS,
        system_code=settings.SYSTEM_CODE,
        max_bytes=settings.LOG_MAX_BYTES,
    )
    store = await create_store_when_ready(logger)
    await BatchCoordinator(store, build_batch_registry()).run_forever()


if __name__ == "__main__":
    asyncio.run(main())
