from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from .definition import BatchTaskSpec
from .models import BatchOptions, BatchRecord, BatchStatus, TaskRecord, TaskStatus
from .results import TaskExecutionResult, TaskResultKind


TERMINAL_BATCH_STATUSES = {
    BatchStatus.SUCCEEDED,
    BatchStatus.PARTIAL_FAILED,
    BatchStatus.FAILED,
    BatchStatus.CANCELLED,
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class RedisBatchStore:
    """Redis-backed source of truth and durable queues for the batch engine."""

    def __init__(self, redis_client, *, key_prefix: str, retention_seconds: int = 30 * 86400):
        self.r = redis_client
        self.prefix = key_prefix.rstrip(":")
        self.retention_seconds = retention_seconds

    def _key(self, *parts: str) -> str:
        return ":".join((self.prefix, *parts))

    def _batch_key(self, batch_id: str) -> str:
        return self._key("batch", batch_id)

    def _task_key(self, task_id: str) -> str:
        return self._key("task", task_id)

    def _task_ids_key(self, batch_id: str) -> str:
        return self._key("batch", batch_id, "task_ids")

    def _business_keys_key(self, batch_id: str) -> str:
        return self._key("batch", batch_id, "business_keys")

    def _lock(self, batch_id: str):
        return self.r.lock(self._key("lock", "batch", batch_id), timeout=30, blocking_timeout=5)

    @property
    def command_queue_key(self) -> str:
        return self._key("queue", "commands")

    @property
    def ready_queue_key(self) -> str:
        return self._key("queue", "ready")

    @property
    def processing_queue_key(self) -> str:
        return self._key("queue", "processing")

    @property
    def command_processing_queue_key(self) -> str:
        return self._key("queue", "commands", "processing")

    @property
    def retry_key(self) -> str:
        return self._key("schedule", "retry")

    @property
    def leased_key(self) -> str:
        return self._key("schedule", "leased")

    @property
    def scheduled_batches_key(self) -> str:
        return self._key("schedule", "batches")

    async def create_batch(
        self,
        *,
        batch_type: str,
        definition_version: str,
        batch_input: dict[str, Any],
        options: BatchOptions,
        idempotency_key: str | None,
        scheduled_at: str | None = None,
    ) -> tuple[BatchRecord, bool]:
        return await asyncio.to_thread(
            self._create_batch,
            batch_type,
            definition_version,
            batch_input,
            options,
            idempotency_key,
            scheduled_at,
        )

    def _create_batch(
        self,
        batch_type: str,
        definition_version: str,
        batch_input: dict[str, Any],
        options: BatchOptions,
        idempotency_key: str | None,
        scheduled_at: str | None,
    ) -> tuple[BatchRecord, bool]:
        if idempotency_key:
            digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
            idem_key = self._key("idempotency", digest)
            with self.r.lock(self._key("lock", "idempotency", digest), timeout=30, blocking_timeout=5):
                existing_id = self.r.get(idem_key)
                existing = self._get_batch(existing_id) if existing_id else None
                if existing:
                    return existing, False
                record = self._new_batch_record(
                    batch_type, definition_version, batch_input, options, scheduled_at
                )
                self._persist_new_batch(record, idem_key=idem_key)
                return record, True

        record = self._new_batch_record(
            batch_type, definition_version, batch_input, options, scheduled_at
        )
        self._persist_new_batch(record)
        return record, True

    def _new_batch_record(
        self,
        batch_type: str,
        definition_version: str,
        batch_input: dict[str, Any],
        options: BatchOptions,
        scheduled_at: str | None,
    ) -> BatchRecord:
        scheduled_timestamp = datetime.fromisoformat(scheduled_at).timestamp() if scheduled_at else None
        if scheduled_timestamp is not None and scheduled_timestamp <= time.time():
            raise ValueError("scheduled_at must be in the future")
        is_future = scheduled_timestamp is not None
        return BatchRecord(
            batch_id=f"bat_{uuid.uuid4().hex}",
            batch_type=batch_type,
            definition_version=definition_version,
            status=BatchStatus.SCHEDULED if is_future else BatchStatus.CREATED,
            input=batch_input,
            options=options,
            created_at=utc_now(),
            scheduled_at=scheduled_at,
        )

    def _persist_new_batch(self, record: BatchRecord, *, idem_key: str | None = None) -> None:
        batch_id = record.batch_id
        ttl = self._batch_ttl(record)
        pipe = self.r.pipeline(transaction=True)
        if idem_key:
            pipe.set(idem_key, batch_id, ex=ttl)
        pipe.set(self._batch_key(batch_id), record.model_dump_json(), ex=ttl)
        pipe.zadd(self._key("batches"), {batch_id: time.time()})
        if record.status == BatchStatus.SCHEDULED:
            pipe.zadd(
                self.scheduled_batches_key,
                {batch_id: datetime.fromisoformat(record.scheduled_at).timestamp()},
            )
        else:
            pipe.lpush(self.command_queue_key, json.dumps({"action": "prepare", "batch_id": batch_id}))
        pipe.execute()

    async def get_batch(self, batch_id: str) -> BatchRecord | None:
        return await asyncio.to_thread(self._get_batch, batch_id)

    def _get_batch(self, batch_id: str) -> BatchRecord | None:
        raw = self.r.get(self._batch_key(batch_id))
        return BatchRecord.model_validate_json(raw) if raw else None

    def _save_batch(self, batch: BatchRecord) -> None:
        self.r.set(
            self._batch_key(batch.batch_id),
            batch.model_dump_json(),
            ex=self._batch_ttl(batch),
        )

    def _batch_ttl(self, batch: BatchRecord) -> int:
        if not batch.scheduled_at or batch.status != BatchStatus.SCHEDULED:
            return self.retention_seconds
        seconds_until_start = max(
            0,
            int(datetime.fromisoformat(batch.scheduled_at).timestamp() - time.time()),
        )
        return self.retention_seconds + seconds_until_start

    async def get_task(self, task_id: str) -> TaskRecord | None:
        return await asyncio.to_thread(self._get_task, task_id)

    def _get_task(self, task_id: str) -> TaskRecord | None:
        raw = self.r.get(self._task_key(task_id))
        return TaskRecord.model_validate_json(raw) if raw else None

    def _save_task(self, task: TaskRecord) -> None:
        self.r.set(self._task_key(task.task_id), task.model_dump_json(), ex=self.retention_seconds)

    async def list_batches(self, *, offset: int = 0, limit: int = 50) -> tuple[list[BatchRecord], int | None]:
        return await asyncio.to_thread(self._list_batches, offset, limit)

    def _list_batches(self, offset: int, limit: int) -> tuple[list[BatchRecord], int | None]:
        ids = self.r.zrevrange(self._key("batches"), offset, offset + limit)
        items = [item for batch_id in ids[:limit] if (item := self._get_batch(batch_id))]
        return items, offset + limit if len(ids) > limit else None

    async def list_tasks(self, batch_id: str, *, offset: int = 0, limit: int = 50) -> tuple[list[TaskRecord], int | None]:
        return await asyncio.to_thread(self._list_tasks, batch_id, offset, limit)

    def _list_tasks(self, batch_id: str, offset: int, limit: int) -> tuple[list[TaskRecord], int | None]:
        ids = self.r.lrange(self._task_ids_key(batch_id), offset, offset + limit)
        items = [item for task_id in ids[:limit] if (item := self._get_task(task_id))]
        return items, offset + limit if len(ids) > limit else None

    async def transition_batch(
        self,
        batch_id: str,
        *,
        expected: set[BatchStatus],
        target: BatchStatus,
        error: str | None = None,
    ) -> BatchRecord | None:
        return await asyncio.to_thread(self._transition_batch, batch_id, expected, target, error)

    def _transition_batch(
        self, batch_id: str, expected: set[BatchStatus], target: BatchStatus, error: str | None
    ) -> BatchRecord | None:
        with self._lock(batch_id):
            batch = self._get_batch(batch_id)
            if not batch or batch.status not in expected:
                return None
            batch.status = target
            batch.error = error
            if target == BatchStatus.PREPARING and not batch.started_at:
                batch.started_at = utc_now()
            if target in TERMINAL_BATCH_STATUSES:
                batch.finished_at = utc_now()
            self._save_batch(batch)
            return batch

    async def set_runtime_context(self, batch_id: str, runtime_context: dict[str, Any]) -> None:
        await asyncio.to_thread(self._set_runtime_context, batch_id, runtime_context)

    def _set_runtime_context(self, batch_id: str, runtime_context: dict[str, Any]) -> None:
        with self._lock(batch_id):
            batch = self._get_batch(batch_id)
            if not batch:
                raise KeyError(batch_id)
            batch.runtime_context = runtime_context
            self._save_batch(batch)

    async def create_task(self, batch_id: str, spec: BatchTaskSpec) -> TaskRecord | None:
        return await asyncio.to_thread(self._create_task, batch_id, spec)

    def _create_task(self, batch_id: str, spec: BatchTaskSpec) -> TaskRecord | None:
        if not spec.business_key:
            raise ValueError("task business_key is required")
        with self._lock(batch_id):
            batch = self._get_batch(batch_id)
            if not batch or batch.status not in {BatchStatus.GENERATING_TASKS, BatchStatus.RUNNING}:
                return None
            if not self.r.sadd(self._business_keys_key(batch_id), spec.business_key):
                return None
            task = TaskRecord(
                task_id=f"tsk_{uuid.uuid4().hex}",
                batch_id=batch_id,
                business_key=spec.business_key,
                status=TaskStatus.PENDING,
                payload=spec.payload,
                max_attempts=batch.options.max_attempts,
                created_at=utc_now(),
            )
            batch.total += 1
            batch.pending += 1
            pipe = self.r.pipeline(transaction=True)
            pipe.set(self._task_key(task.task_id), task.model_dump_json(), ex=self.retention_seconds)
            pipe.rpush(self._task_ids_key(batch_id), task.task_id)
            pipe.expire(self._task_ids_key(batch_id), self.retention_seconds)
            pipe.expire(self._business_keys_key(batch_id), self.retention_seconds)
            pipe.lpush(self.ready_queue_key, task.task_id)
            pipe.set(self._batch_key(batch_id), batch.model_dump_json(), ex=self.retention_seconds)
            pipe.execute()
            return task

    async def mark_generation_complete(self, batch_id: str) -> BatchRecord:
        return await asyncio.to_thread(self._mark_generation_complete, batch_id)

    def _mark_generation_complete(self, batch_id: str) -> BatchRecord:
        with self._lock(batch_id):
            batch = self._get_batch(batch_id)
            if not batch:
                raise KeyError(batch_id)
            batch.generation_complete = True
            if batch.status == BatchStatus.GENERATING_TASKS:
                batch.status = BatchStatus.RUNNING
            self._save_batch(batch)
            if batch.total == 0 and batch.status == BatchStatus.RUNNING:
                self._enqueue_command("finalize", batch_id)
            return batch

    def _enqueue_command(self, action: str, batch_id: str) -> None:
        self.r.lpush(self.command_queue_key, json.dumps({"action": action, "batch_id": batch_id}))

    async def dequeue_command(self, timeout: int = 1) -> dict[str, str] | None:
        raw = await asyncio.to_thread(
            self.r.brpoplpush,
            self.command_queue_key,
            self.command_processing_queue_key,
            timeout,
        )
        if not raw:
            return None
        command = json.loads(raw)
        command["_raw"] = raw
        return command

    async def acknowledge_command(self, raw: str) -> None:
        await asyncio.to_thread(self.r.lrem, self.command_processing_queue_key, 1, raw)

    async def recover_commands(self) -> int:
        return await asyncio.to_thread(self._recover_commands)

    def _recover_commands(self) -> int:
        count = 0
        while self.r.rpoplpush(self.command_processing_queue_key, self.command_queue_key):
            count += 1
        return count

    async def claim_task(self, worker_id: str, timeout: int = 1) -> TaskRecord | None:
        task_id = await asyncio.to_thread(
            self.r.brpoplpush, self.ready_queue_key, self.processing_queue_key, timeout
        )
        if not task_id:
            return None
        return await asyncio.to_thread(self._reserve_task, task_id, worker_id)

    def _reserve_task(self, task_id: str, worker_id: str) -> TaskRecord | None:
        task = self._get_task(task_id)
        if not task:
            self.r.lrem(self.processing_queue_key, 1, task_id)
            return None
        with self._lock(task.batch_id):
            task = self._get_task(task_id)
            batch = self._get_batch(task.batch_id) if task else None
            if not task or not batch or task.status not in {TaskStatus.PENDING, TaskStatus.RETRY_WAIT}:
                self.r.lrem(self.processing_queue_key, 1, task_id)
                return None
            if batch.status == BatchStatus.PAUSED or batch.running >= batch.options.max_concurrency:
                self.r.lrem(self.processing_queue_key, 1, task_id)
                self.r.zadd(self.retry_key, {task_id: time.time() + 1})
                return None
            if batch.status != BatchStatus.RUNNING:
                self.r.lrem(self.processing_queue_key, 1, task_id)
                if batch.status == BatchStatus.GENERATING_TASKS:
                    self.r.zadd(self.retry_key, {task_id: time.time() + 1})
                elif batch.status in {BatchStatus.CANCELLING, BatchStatus.CANCELLED, BatchStatus.FAILED}:
                    task.status = TaskStatus.CANCELLED
                    task.finished_at = utc_now()
                    batch.pending = max(0, batch.pending - 1)
                    batch.cancelled += 1
                    self._save_task(task)
                    self._save_batch(batch)
                return None
            now = time.time()
            task.status = TaskStatus.RUNNING
            task.attempt += 1
            task.worker_id = worker_id
            task.started_at = task.started_at or utc_now()
            task.lease_until = now + batch.options.task_timeout_seconds
            batch.pending = max(0, batch.pending - 1)
            batch.running += 1
            pipe = self.r.pipeline(transaction=True)
            pipe.set(self._task_key(task_id), task.model_dump_json(), ex=self.retention_seconds)
            pipe.set(self._batch_key(batch.batch_id), batch.model_dump_json(), ex=self.retention_seconds)
            pipe.zadd(self.leased_key, {task_id: task.lease_until})
            pipe.execute()
            return task

    async def complete_task(self, task_id: str, result: TaskExecutionResult) -> None:
        await asyncio.to_thread(self._complete_task, task_id, result)

    def _complete_task(self, task_id: str, result: TaskExecutionResult) -> None:
        task = self._get_task(task_id)
        if not task:
            return
        with self._lock(task.batch_id):
            task = self._get_task(task_id)
            batch = self._get_batch(task.batch_id) if task else None
            if not task or not batch or task.status != TaskStatus.RUNNING:
                return
            batch.running = max(0, batch.running - 1)
            task.lease_until = None
            task.worker_id = None
            retry_score: float | None = None

            if result.kind == TaskResultKind.SUCCEEDED:
                task.status = TaskStatus.SUCCEEDED
                task.output = result.output
                task.finished_at = utc_now()
                batch.succeeded += 1
            elif batch.status in {BatchStatus.CANCELLING, BatchStatus.CANCELLED}:
                task.status = TaskStatus.CANCELLED
                task.error_code = "BATCH_CANCELLED"
                task.error = "parent batch was cancelled"
                task.finished_at = utc_now()
                batch.cancelled += 1
            elif result.kind == TaskResultKind.RETRYABLE_FAILURE and task.attempt < task.max_attempts:
                task.status = TaskStatus.RETRY_WAIT
                task.error_code = result.error_code
                task.error = result.error
                batch.pending += 1
                retry_after = (
                    result.retry_after_seconds
                    if result.retry_after_seconds is not None
                    else self._retry_delay(batch, task.attempt)
                )
                retry_score = time.time() + retry_after
            else:
                task.status = TaskStatus.FAILED
                task.error_code = result.error_code
                task.error = result.error
                task.finished_at = utc_now()
                batch.failed += 1

            cleanup_needed = False
            if batch.status == BatchStatus.CANCELLING and batch.running == 0:
                batch.status = BatchStatus.CANCELLED
                batch.finished_at = utc_now()
                cleanup_needed = True
            finalize_needed = self._is_finalize_ready(batch)
            pipe = self.r.pipeline(transaction=True)
            pipe.lrem(self.processing_queue_key, 1, task_id)
            pipe.zrem(self.leased_key, task_id)
            if retry_score is not None:
                pipe.zadd(self.retry_key, {task_id: retry_score})
            pipe.set(self._task_key(task_id), task.model_dump_json(), ex=self.retention_seconds)
            pipe.set(self._batch_key(batch.batch_id), batch.model_dump_json(), ex=self.retention_seconds)
            if cleanup_needed:
                pipe.lpush(
                    self.command_queue_key,
                    json.dumps({"action": "cleanup", "batch_id": batch.batch_id}),
                )
            if finalize_needed:
                pipe.lpush(
                    self.command_queue_key,
                    json.dumps({"action": "finalize", "batch_id": batch.batch_id}),
                )
            pipe.execute()

    def _retry_delay(self, batch: BatchRecord, attempt: int) -> int:
        delays = batch.options.retry_delays_seconds
        if not delays:
            return 60
        return max(0, delays[min(attempt - 1, len(delays) - 1)])

    def _enqueue_finalize_if_ready(self, batch: BatchRecord) -> None:
        if self._is_finalize_ready(batch):
            self._enqueue_command("finalize", batch.batch_id)

    def _is_finalize_ready(self, batch: BatchRecord) -> bool:
        terminal = batch.succeeded + batch.failed + batch.cancelled
        return batch.generation_complete and batch.status == BatchStatus.RUNNING and terminal == batch.total

    async def promote_due_retries(self, limit: int = 100) -> int:
        return await asyncio.to_thread(self._promote_due_retries, limit)

    def _promote_due_retries(self, limit: int) -> int:
        ids = self.r.zrangebyscore(self.retry_key, "-inf", time.time(), start=0, num=limit)
        moved = 0
        for task_id in ids:
            if not self.r.zrem(self.retry_key, task_id):
                continue
            task = self._get_task(task_id)
            if task and task.status in {TaskStatus.PENDING, TaskStatus.RETRY_WAIT}:
                self.r.lpush(self.ready_queue_key, task_id)
                moved += 1
        return moved

    async def promote_due_batches(self, limit: int = 100) -> int:
        return await asyncio.to_thread(self._promote_due_batches, limit)

    def _promote_due_batches(self, limit: int) -> int:
        ids = self.r.zrangebyscore(
            self.scheduled_batches_key, "-inf", time.time(), start=0, num=limit
        )
        promoted = 0
        for batch_id in ids:
            with self._lock(batch_id):
                batch = self._get_batch(batch_id)
                if not batch or batch.status != BatchStatus.SCHEDULED:
                    self.r.zrem(self.scheduled_batches_key, batch_id)
                    continue
                batch.status = BatchStatus.CREATED
                pipe = self.r.pipeline(transaction=True)
                pipe.zrem(self.scheduled_batches_key, batch_id)
                pipe.set(
                    self._batch_key(batch_id),
                    batch.model_dump_json(),
                    ex=self._batch_ttl(batch),
                )
                pipe.lpush(
                    self.command_queue_key,
                    json.dumps({"action": "prepare", "batch_id": batch_id}),
                )
                pipe.execute()
                promoted += 1
        return promoted

    async def recover_expired_leases(self, limit: int = 100) -> int:
        return await asyncio.to_thread(self._recover_expired_leases, limit)

    def _recover_expired_leases(self, limit: int) -> int:
        ids = self.r.zrangebyscore(self.leased_key, "-inf", time.time(), start=0, num=limit)
        recovered = 0
        for task_id in ids:
            task = self._get_task(task_id)
            if not task:
                self.r.zrem(self.leased_key, task_id)
                self.r.lrem(self.processing_queue_key, 0, task_id)
                continue
            result = TaskExecutionResult.retryable_failure("TASK_LEASE_EXPIRED", "worker lease expired", retry_after_seconds=0)
            self._complete_task(task_id, result)
            recovered += 1
        return recovered

    async def begin_finalize(self, batch_id: str) -> BatchRecord | None:
        return await asyncio.to_thread(self._begin_finalize, batch_id)

    def _begin_finalize(self, batch_id: str) -> BatchRecord | None:
        with self._lock(batch_id):
            batch = self._get_batch(batch_id)
            if not batch or batch.status != BatchStatus.RUNNING or not batch.generation_complete:
                return None
            if batch.succeeded + batch.failed + batch.cancelled != batch.total:
                return None
            batch.status = BatchStatus.FINALIZING
            self._save_batch(batch)
            return batch

    async def finish_finalize(
        self, batch_id: str, *, result: dict[str, Any] | None = None, error: str | None = None
    ) -> BatchRecord:
        return await asyncio.to_thread(self._finish_finalize, batch_id, result, error)

    def _finish_finalize(
        self, batch_id: str, result: dict[str, Any] | None, error: str | None
    ) -> BatchRecord:
        with self._lock(batch_id):
            batch = self._get_batch(batch_id)
            if not batch:
                raise KeyError(batch_id)
            batch.error = error
            batch.result = result
            batch.status = BatchStatus.FAILED if error else (BatchStatus.PARTIAL_FAILED if batch.failed else BatchStatus.SUCCEEDED)
            batch.finished_at = utc_now()
            self._save_batch(batch)
            return batch

    async def pause_batch(self, batch_id: str) -> BatchRecord | None:
        return await asyncio.to_thread(self._pause_batch, batch_id)

    def _pause_batch(self, batch_id: str) -> BatchRecord | None:
        with self._lock(batch_id):
            batch = self._get_batch(batch_id)
            if not batch or batch.status != BatchStatus.RUNNING:
                return None
            batch.resume_status = batch.status
            batch.status = BatchStatus.PAUSED
            self._save_batch(batch)
            return batch

    async def resume_batch(self, batch_id: str) -> BatchRecord | None:
        return await asyncio.to_thread(self._resume_batch, batch_id)

    def _resume_batch(self, batch_id: str) -> BatchRecord | None:
        with self._lock(batch_id):
            batch = self._get_batch(batch_id)
            if not batch or batch.status != BatchStatus.PAUSED:
                return None
            batch.status = batch.resume_status or BatchStatus.RUNNING
            batch.resume_status = None
            self._save_batch(batch)
            self._enqueue_finalize_if_ready(batch)
            return batch

    async def cancel_batch(self, batch_id: str) -> BatchRecord | None:
        return await asyncio.to_thread(self._cancel_batch, batch_id)

    def _cancel_batch(self, batch_id: str) -> BatchRecord | None:
        with self._lock(batch_id):
            batch = self._get_batch(batch_id)
            if not batch or batch.status not in {
                BatchStatus.SCHEDULED,
                BatchStatus.RUNNING,
                BatchStatus.PAUSED,
            }:
                return None
            if batch.status == BatchStatus.SCHEDULED:
                batch.status = BatchStatus.CANCELLED
                batch.finished_at = utc_now()
                pipe = self.r.pipeline(transaction=True)
                pipe.zrem(self.scheduled_batches_key, batch_id)
                pipe.set(
                    self._batch_key(batch_id),
                    batch.model_dump_json(),
                    ex=self.retention_seconds,
                )
                pipe.execute()
                return batch
            batch.status = BatchStatus.CANCELLING
            ids = self.r.lrange(self._task_ids_key(batch_id), 0, -1)
            for task_id in ids:
                task = self._get_task(task_id)
                if not task or task.status not in {TaskStatus.PENDING, TaskStatus.RETRY_WAIT}:
                    continue
                task.status = TaskStatus.CANCELLED
                task.finished_at = utc_now()
                batch.pending = max(0, batch.pending - 1)
                batch.cancelled += 1
                self._save_task(task)
                self.r.lrem(self.ready_queue_key, 0, task_id)
                self.r.zrem(self.retry_key, task_id)
            if batch.running == 0:
                batch.status = BatchStatus.CANCELLED
                batch.finished_at = utc_now()
                self._enqueue_command("cleanup", batch_id)
            self._save_batch(batch)
            return batch

    async def reschedule_batch(self, batch_id: str, scheduled_at: str) -> BatchRecord | None:
        return await asyncio.to_thread(self._reschedule_batch, batch_id, scheduled_at)

    def _reschedule_batch(self, batch_id: str, scheduled_at: str) -> BatchRecord | None:
        timestamp = datetime.fromisoformat(scheduled_at).timestamp()
        if timestamp <= time.time():
            raise ValueError("scheduled_at must be in the future")
        with self._lock(batch_id):
            batch = self._get_batch(batch_id)
            if not batch or batch.status != BatchStatus.SCHEDULED:
                return None
            batch.scheduled_at = scheduled_at
            pipe = self.r.pipeline(transaction=True)
            pipe.set(
                self._batch_key(batch_id),
                batch.model_dump_json(),
                ex=self._batch_ttl(batch),
            )
            pipe.zadd(self.scheduled_batches_key, {batch_id: timestamp})
            pipe.execute()
            return batch

    async def retry_failed_tasks(self, batch_id: str) -> BatchRecord | None:
        return await asyncio.to_thread(self._retry_failed_tasks, batch_id)

    def _retry_failed_tasks(self, batch_id: str) -> BatchRecord | None:
        with self._lock(batch_id):
            batch = self._get_batch(batch_id)
            if not batch or batch.status != BatchStatus.PARTIAL_FAILED:
                return None
            ids = self.r.lrange(self._task_ids_key(batch_id), 0, -1)
            retried = 0
            pipe = self.r.pipeline(transaction=True)
            for task_id in ids:
                task = self._get_task(task_id)
                if not task or task.status != TaskStatus.FAILED:
                    continue
                task.status = TaskStatus.PENDING
                task.attempt = 0
                task.output = None
                task.error_code = None
                task.error = None
                task.finished_at = None
                pipe.set(self._task_key(task_id), task.model_dump_json(), ex=self.retention_seconds)
                pipe.lpush(self.ready_queue_key, task_id)
                retried += 1
            if not retried:
                return batch
            batch.failed = max(0, batch.failed - retried)
            batch.pending += retried
            batch.status = BatchStatus.RUNNING
            batch.finished_at = None
            batch.error = None
            batch.result = None
            pipe.set(self._batch_key(batch_id), batch.model_dump_json(), ex=self.retention_seconds)
            pipe.execute()
            return batch
