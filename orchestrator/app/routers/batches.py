from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import ValidationError

from ..batch.schemas import (
    BatchListResponse,
    CreateBatchRequest,
    CreateBatchResponse,
    RescheduleBatchRequest,
    TaskListResponse,
)
from ..batch.service import BatchService
from ..batch.store import RedisBatchStore
from ..config import settings


router = APIRouter(prefix="/batches")


def _get_store(request: Request) -> RedisBatchStore:
    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is None:
        raise HTTPException(status_code=503, detail="batch_store_unavailable")
    return RedisBatchStore(
        redis_client,
        key_prefix=settings.BATCH_REDIS_KEY_PREFIX,
        retention_seconds=settings.BATCH_RETENTION_SEC,
    )


def _get_service(request: Request) -> BatchService:
    return BatchService(_get_store(request), request.app.state.batch_registry)


@router.post("", response_model=CreateBatchResponse, status_code=202)
async def create_batch(
    payload: CreateBatchRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    service = _get_service(request)
    try:
        batch, created = await service.create_batch(
            batch_type=payload.batch_type,
            batch_input=payload.input,
            options=payload.options,
            idempotency_key=idempotency_key,
            scheduled_at=payload.scheduled_at,
        )
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CreateBatchResponse(batch_id=batch.batch_id, status=batch.status, created=created)


@router.get("", response_model=BatchListResponse)
async def list_batches(
    request: Request,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    items, next_cursor = await _get_store(request).list_batches(offset=cursor, limit=limit)
    return BatchListResponse(items=items, next_cursor=next_cursor)


@router.get("/{batch_id}")
async def get_batch(batch_id: str, request: Request):
    batch = await _get_store(request).get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="batch_not_found")
    return batch


@router.get("/{batch_id}/tasks", response_model=TaskListResponse)
async def list_tasks(
    batch_id: str,
    request: Request,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    store = _get_store(request)
    if not await store.get_batch(batch_id):
        raise HTTPException(status_code=404, detail="batch_not_found")
    items, next_cursor = await store.list_tasks(batch_id, offset=cursor, limit=limit)
    return TaskListResponse(items=items, next_cursor=next_cursor)


@router.get("/{batch_id}/tasks/{task_id}")
async def get_task(batch_id: str, task_id: str, request: Request):
    task = await _get_store(request).get_task(task_id)
    if not task or task.batch_id != batch_id:
        raise HTTPException(status_code=404, detail="task_not_found")
    return task


async def _control(batch_id: str, request: Request, operation: str):
    store = _get_store(request)
    if not await store.get_batch(batch_id):
        raise HTTPException(status_code=404, detail="batch_not_found")
    method = getattr(store, operation)
    batch = await method(batch_id)
    if not batch:
        raise HTTPException(status_code=409, detail=f"batch_cannot_{operation.removesuffix('_batch')}")
    return batch


@router.post("/{batch_id}/pause")
async def pause_batch(batch_id: str, request: Request):
    return await _control(batch_id, request, "pause_batch")


@router.post("/{batch_id}/resume")
async def resume_batch(batch_id: str, request: Request):
    return await _control(batch_id, request, "resume_batch")


@router.post("/{batch_id}/cancel")
async def cancel_batch(batch_id: str, request: Request):
    return await _control(batch_id, request, "cancel_batch")


@router.post("/{batch_id}/retry-failed")
async def retry_failed_tasks(batch_id: str, request: Request):
    return await _control(batch_id, request, "retry_failed_tasks")


@router.post("/{batch_id}/reschedule")
async def reschedule_batch(batch_id: str, payload: RescheduleBatchRequest, request: Request):
    store = _get_store(request)
    if not await store.get_batch(batch_id):
        raise HTTPException(status_code=404, detail="batch_not_found")
    try:
        batch = await store.reschedule_batch(batch_id, payload.scheduled_at.isoformat())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not batch:
        raise HTTPException(status_code=409, detail="only_scheduled_batches_can_be_rescheduled")
    return batch
