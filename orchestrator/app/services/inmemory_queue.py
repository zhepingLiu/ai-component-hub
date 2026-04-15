from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable


logger = logging.getLogger("orchestrator.queue")


class InMemoryJobQueue:
    def __init__(self, *, maxsize: int, consumer_count: int):
        self.maxsize = max(1, int(maxsize))
        self.consumer_count = max(1, int(consumer_count))
        self._queue: asyncio.Queue[tuple[str, Callable[[], Awaitable[None]]]] = asyncio.Queue(maxsize=self.maxsize)
        self._consumer_tasks: list[asyncio.Task] = []
        self._running = 0
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._consumer_tasks:
            return
        for idx in range(self.consumer_count):
            task = asyncio.create_task(self._consumer_loop(idx + 1))
            self._consumer_tasks.append(task)

    async def stop(self) -> None:
        for task in self._consumer_tasks:
            task.cancel()
        if self._consumer_tasks:
            await asyncio.gather(*self._consumer_tasks, return_exceptions=True)
        self._consumer_tasks.clear()

    def try_enqueue_nowait(self, request_id: str, factory: Callable[[], Awaitable[None]]) -> bool:
        try:
            self._queue.put_nowait((request_id, factory))
            return True
        except asyncio.QueueFull:
            return False

    async def snapshot(self) -> dict[str, int]:
        async with self._lock:
            return {
                "queue_size": self._queue.qsize(),
                "queue_maxsize": self.maxsize,
                "running": self._running,
                "consumer_count": self.consumer_count,
            }

    async def _consumer_loop(self, consumer_id: int) -> None:
        while True:
            request_id, factory = await self._queue.get()
            async with self._lock:
                self._running += 1
            try:
                logger.info({"event": "queue.dequeued", "request_id": request_id, "consumer_id": consumer_id})
                await factory()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(
                    {
                        "event": "queue.job_failed",
                        "request_id": request_id,
                        "consumer_id": consumer_id,
                        "error": str(exc),
                    }
                )
            finally:
                async with self._lock:
                    self._running -= 1
                self._queue.task_done()
