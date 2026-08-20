from __future__ import annotations

from typing import Any
from datetime import datetime

from .models import BatchOptions, BatchRecord
from .registry import BatchDefinitionRegistry
from .store import RedisBatchStore


class BatchService:
    def __init__(self, store: RedisBatchStore, registry: BatchDefinitionRegistry):
        self.store = store
        self.registry = registry

    async def create_batch(
        self,
        *,
        batch_type: str,
        batch_input: dict[str, Any],
        options: BatchOptions | None,
        idempotency_key: str | None,
        scheduled_at: datetime | None = None,
    ) -> tuple[BatchRecord, bool]:
        definition = self.registry.get(batch_type)
        normalized_input = await definition.validate_input(batch_input)
        effective_options = options or definition.default_options.model_copy(deep=True)
        return await self.store.create_batch(
            batch_type=batch_type,
            definition_version=definition.version,
            batch_input=normalized_input,
            options=effective_options,
            idempotency_key=idempotency_key,
            scheduled_at=scheduled_at.isoformat() if scheduled_at else None,
        )
