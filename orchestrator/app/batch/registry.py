from __future__ import annotations

from .definition import BaseBatchDefinition


class BatchDefinitionRegistry:
    def __init__(self) -> None:
        self._definitions: dict[tuple[str, str], BaseBatchDefinition] = {}
        self._latest: dict[str, str] = {}

    def register(self, definition: BaseBatchDefinition) -> None:
        if not definition.batch_type:
            raise ValueError("batch_type is required")
        key = (definition.batch_type, definition.version)
        if key in self._definitions:
            raise ValueError(f"duplicate batch definition: {definition.batch_type}@{definition.version}")
        self._definitions[key] = definition
        self._latest[definition.batch_type] = definition.version

    def get(self, batch_type: str, version: str | None = None) -> BaseBatchDefinition:
        resolved_version = version or self._latest.get(batch_type)
        try:
            return self._definitions[(batch_type, str(resolved_version))]
        except KeyError as exc:
            suffix = f"@{version}" if version else ""
            raise KeyError(f"unknown batch definition: {batch_type}{suffix}") from exc

    def contains(self, batch_type: str) -> bool:
        return batch_type in self._latest

    def list_types(self) -> list[str]:
        return sorted(self._latest)
