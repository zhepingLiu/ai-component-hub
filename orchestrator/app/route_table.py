from __future__ import annotations

from pathlib import Path
from typing import Awaitable, Protocol, TypeVar, cast

import yaml


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


class YamlRouteTable:
    def __init__(self, route_file: str):
        self.route_file = Path(route_file)
        self._routes: dict[str, str] = {}
        self.reload()

    def _load(self) -> dict[str, str]:
        if not self.route_file.exists():
            raise FileNotFoundError(f"Routes file not found: {self.route_file}")
        with self.route_file.open("r") as f:
            data = yaml.safe_load(f) or {}
        return {str(k): str(v) for k, v in data.items()}

    def _persist(self) -> None:
        self.route_file.parent.mkdir(parents=True, exist_ok=True)
        with self.route_file.open("w") as f:
            yaml.safe_dump(self._routes, f, sort_keys=True)

    def reload(self) -> None:
        self._routes = self._load()

    def resolve(self, category: str, action: str) -> str | None:
        return self._routes.get(f"{category}.{action}")

    def add(self, key: str, value: str) -> None:
        self._routes[key] = value
        self._persist()

    def all(self) -> dict:
        return dict(self._routes)
