from __future__ import annotations

import unittest
import sys
from datetime import UTC, datetime, timedelta
from collections import defaultdict
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace

fake_config = ModuleType("app.config")
fake_config.settings = SimpleNamespace(
    BATCH_MAINTENANCE_INTERVAL_SEC=1.0,
    BATCH_COORDINATOR_CONCURRENCY=1,
    BATCH_REDIS_KEY_PREFIX="test:batch",
    BATCH_RETENTION_SEC=3600,
    BATCH_WORKER_CONCURRENCY=1,
)
sys.modules.setdefault("app.config", fake_config)
fake_redis_client = ModuleType("app.redis_client")
fake_redis_client.create_redis_client = lambda: None
sys.modules.setdefault("app.redis_client", fake_redis_client)

from app.batch.coordinator import BatchCoordinator
from app.batch.definitions import build_batch_registry
from app.batch.models import BatchOptions, BatchStatus, TaskStatus
from app.batch.results import TaskExecutionResult
from app.batch.service import BatchService
from app.batch.store import RedisBatchStore
from app.batch.worker import BatchWorker


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = defaultdict(list)
        self.sets: dict[str, set[str]] = defaultdict(set)
        self.zsets: dict[str, dict[str, float]] = defaultdict(dict)

    def lock(self, *args, **kwargs):
        return nullcontext()

    def pipeline(self, transaction=True):
        return self

    def execute(self):
        return []

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def expire(self, key, seconds):
        return True

    def sadd(self, key, value):
        before = len(self.sets[key])
        self.sets[key].add(value)
        return int(len(self.sets[key]) != before)

    def rpush(self, key, *values):
        self.lists[key].extend(values)
        return len(self.lists[key])

    def lpush(self, key, *values):
        for value in values:
            self.lists[key].insert(0, value)
        return len(self.lists[key])

    def lrange(self, key, start, end):
        values = self.lists[key]
        if end == -1:
            return values[start:]
        return values[start : end + 1]

    def lrem(self, key, count, value):
        values = self.lists[key]
        removed = 0
        indexes = range(len(values) - 1, -1, -1) if count < 0 else range(len(values))
        for index in list(indexes):
            if values[index] == value and (count == 0 or removed < abs(count)):
                values.pop(index)
                removed += 1
        return removed

    def brpoplpush(self, source, destination, timeout=0):
        return self.rpoplpush(source, destination)

    def rpoplpush(self, source, destination):
        if not self.lists[source]:
            return None
        value = self.lists[source].pop()
        self.lists[destination].insert(0, value)
        return value

    def zadd(self, key, mapping):
        self.zsets[key].update(mapping)
        return len(mapping)

    def zrem(self, key, value):
        return int(self.zsets[key].pop(value, None) is not None)

    def zrevrange(self, key, start, end):
        items = sorted(self.zsets[key].items(), key=lambda item: item[1], reverse=True)
        return [key for key, _ in items[start : end + 1]]

    def zrangebyscore(self, key, minimum, maximum, start=0, num=None):
        minimum = float("-inf") if minimum == "-inf" else float(minimum)
        maximum = float(maximum)
        items = [item for item in self.zsets[key].items() if minimum <= item[1] <= maximum]
        values = [value for value, _ in sorted(items, key=lambda item: item[1])]
        return values[start : start + num if num is not None else None]


class BatchKernelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.redis = FakeRedis()
        self.store = RedisBatchStore(self.redis, key_prefix="test:batch", retention_seconds=3600)
        self.registry = build_batch_registry()
        self.service = BatchService(self.store, self.registry)

    async def _create(self, items, *, idempotency_key=None, options=None, scheduled_at=None):
        return await self.service.create_batch(
            batch_type="echo_batch",
            batch_input={"items": items},
            options=options,
            idempotency_key=idempotency_key,
            scheduled_at=scheduled_at,
        )

    async def test_echo_batch_runs_end_to_end(self) -> None:
        batch, created = await self._create([{"id": "a", "value": 1}, {"id": "b", "value": 2}])
        self.assertTrue(created)

        coordinator = BatchCoordinator(self.store, self.registry)
        await coordinator.handle("prepare", batch.batch_id)
        generated = await self.store.get_batch(batch.batch_id)
        self.assertEqual(generated.status, BatchStatus.RUNNING)
        self.assertEqual(generated.total, 2)

        worker = BatchWorker(self.store, self.registry, worker_id="test-worker")
        for _ in range(2):
            task = await self.store.claim_task("test-worker", timeout=0)
            self.assertIsNotNone(task)
            await worker._execute(task.task_id)

        await coordinator.handle("finalize", batch.batch_id)
        completed = await self.store.get_batch(batch.batch_id)
        self.assertEqual(completed.status, BatchStatus.SUCCEEDED)
        self.assertEqual(completed.succeeded, 2)
        self.assertEqual(completed.result, {"total": 2, "succeeded": 2, "failed": 0})

    async def test_idempotency_key_returns_original_batch(self) -> None:
        first, first_created = await self._create([1], idempotency_key="same-request")
        second, second_created = await self._create([2], idempotency_key="same-request")
        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first.batch_id, second.batch_id)
        self.assertEqual(second.input["items"], [1])

    async def test_retryable_failure_is_requeued(self) -> None:
        options = BatchOptions(max_attempts=2, retry_delays_seconds=[0])
        batch, _ = await self._create([{"id": "a"}], options=options)
        coordinator = BatchCoordinator(self.store, self.registry)
        await coordinator.handle("prepare", batch.batch_id)

        task = await self.store.claim_task("test-worker", timeout=0)
        await self.store.complete_task(
            task.task_id,
            TaskExecutionResult.retryable_failure("TEMPORARY", "try again", retry_after_seconds=0),
        )
        waiting = await self.store.get_task(task.task_id)
        self.assertEqual(waiting.status, TaskStatus.RETRY_WAIT)

        await self.store.promote_due_retries()
        retried = await self.store.claim_task("test-worker", timeout=0)
        self.assertEqual(retried.attempt, 2)
        await self.store.complete_task(retried.task_id, TaskExecutionResult.succeeded({"ok": True}))
        finished = await self.store.get_task(task.task_id)
        self.assertEqual(finished.status, TaskStatus.SUCCEEDED)

    async def test_duplicate_business_keys_are_deduplicated(self) -> None:
        batch, _ = await self._create([{"id": "same"}, {"id": "same"}])
        await BatchCoordinator(self.store, self.registry).handle("prepare", batch.batch_id)
        generated = await self.store.get_batch(batch.batch_id)
        self.assertEqual(generated.total, 1)

    async def test_cancel_marks_pending_tasks_cancelled(self) -> None:
        batch, _ = await self._create([{"id": "a"}, {"id": "b"}])
        await BatchCoordinator(self.store, self.registry).handle("prepare", batch.batch_id)

        cancelled = await self.store.cancel_batch(batch.batch_id)
        self.assertEqual(cancelled.status, BatchStatus.CANCELLED)
        self.assertEqual(cancelled.cancelled, 2)
        tasks, _ = await self.store.list_tasks(batch.batch_id)
        self.assertEqual({task.status for task in tasks}, {TaskStatus.CANCELLED})

    async def test_pause_and_resume_preserve_running_batch(self) -> None:
        batch, _ = await self._create([{"id": "a"}])
        await BatchCoordinator(self.store, self.registry).handle("prepare", batch.batch_id)
        paused = await self.store.pause_batch(batch.batch_id)
        self.assertEqual(paused.status, BatchStatus.PAUSED)
        resumed = await self.store.resume_batch(batch.batch_id)
        self.assertEqual(resumed.status, BatchStatus.RUNNING)

    async def test_scheduled_batch_is_promoted_when_due(self) -> None:
        future = datetime.now(UTC) + timedelta(hours=2)
        batch, _ = await self._create([{"id": "a"}], scheduled_at=future)
        self.assertEqual(batch.status, BatchStatus.SCHEDULED)
        self.assertFalse(self.redis.lists[self.store.command_queue_key])

        self.redis.zsets[self.store.scheduled_batches_key][batch.batch_id] = 0
        promoted = await self.store.promote_due_batches()
        self.assertEqual(promoted, 1)
        due = await self.store.get_batch(batch.batch_id)
        self.assertEqual(due.status, BatchStatus.CREATED)

        await BatchCoordinator(self.store, self.registry).handle("prepare", batch.batch_id)
        running = await self.store.get_batch(batch.batch_id)
        self.assertEqual(running.status, BatchStatus.RUNNING)

    async def test_scheduled_batch_can_be_cancelled_before_start(self) -> None:
        future = datetime.now(UTC) + timedelta(hours=2)
        batch, _ = await self._create([{"id": "a"}], scheduled_at=future)
        cancelled = await self.store.cancel_batch(batch.batch_id)
        self.assertEqual(cancelled.status, BatchStatus.CANCELLED)
        self.assertNotIn(batch.batch_id, self.redis.zsets[self.store.scheduled_batches_key])

    async def test_scheduled_batch_can_be_rescheduled(self) -> None:
        initial = datetime.now(UTC) + timedelta(hours=2)
        updated = datetime.now(UTC) + timedelta(hours=4)
        batch, _ = await self._create([{"id": "a"}], scheduled_at=initial)
        rescheduled = await self.store.reschedule_batch(batch.batch_id, updated.isoformat())
        self.assertEqual(rescheduled.status, BatchStatus.SCHEDULED)
        self.assertEqual(rescheduled.scheduled_at, updated.isoformat())
        self.assertEqual(
            self.redis.zsets[self.store.scheduled_batches_key][batch.batch_id],
            updated.timestamp(),
        )


if __name__ == "__main__":
    unittest.main()
