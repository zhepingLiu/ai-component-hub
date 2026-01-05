from __future__ import annotations

from typing import Awaitable, Protocol, TypeVar, cast


T = TypeVar("T")


class SyncRedis(Protocol):
    def hget(self, name: str, key: str) -> str | None | Awaitable[str | None]: ...
    def hset(self, name: str, key: str, value: str) -> int | Awaitable[int]: ...
    def hgetall(self, name: str) -> dict | Awaitable[dict]: ...


def _ensure_sync(value: T | Awaitable[T], op: str) -> T:
    if hasattr(value, "__await__"):
        raise RuntimeError(f"RouteTable requires sync redis client; got awaitable from {op}")
    return cast(T, value)


class RouteTable:
    def __init__(self, redis_client: SyncRedis, key_prefix: str):
        self.r = redis_client
        self.redis_key = f"{key_prefix}:routes"

    def resolve(self, category: str, action: str) -> str | None:
        value = self.r.hget(self.redis_key, f"{category}.{action}")
        return _ensure_sync(value, "hget")

    def add(self, key: str, value: str) -> None:
        result = self.r.hset(self.redis_key, key, value)
        _ensure_sync(result, "hset")

    def all(self) -> dict:
        value = self.r.hgetall(self.redis_key)
        return _ensure_sync(value, "hgetall") or {}
