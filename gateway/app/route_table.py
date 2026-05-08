import logging
from pathlib import Path

import redis
import yaml

from .config import Settings, settings as app_settings

logger = logging.getLogger("gateway")


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

class RouteTable:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or app_settings
        self.redis_key = f"{self.settings.REDIS_KEY_PREFIX}:routes"
        # decode_responses=True 让读到的是 str 而不是 bytes
        self.r = redis.Redis(
            host=self.settings.REDIS_HOST,
            port=self.settings.REDIS_PORT,
            db=self.settings.REDIS_DB,
            password=self.settings.REDIS_PASSWORD,
            decode_responses=True,
            socket_connect_timeout=self.settings.REDIS_SOCKET_CONNECT_TIMEOUT,
            socket_timeout=self.settings.REDIS_SOCKET_TIMEOUT,
            retry_on_timeout=True,
        )
        self._routes = {}
        if self.settings.ROUTES_PRELOAD_ON_STARTUP:
            self.reload()

    # ------------------------------------------------------------------
    # 🔄 reload(): 从 Redis 同步整个路由表
    # ------------------------------------------------------------------
    def reload(self):
        try:
            routes = self.r.hgetall(self.redis_key) or {}
        except Exception as exc:
            logger.exception(
                {
                    "event": "routes.reload_failed",
                    "redis_key": self.redis_key,
                    "error": str(exc),
                    "cached_routes": len(self._routes),
                }
            )
            return
        self._routes = routes

    # ------------------------------------------------------------------
    # 🔍 resolve(): 根据 category + action 得到 URL
    # ------------------------------------------------------------------
    def resolve(self, category: str, action: str) -> str | None:
        key = f"{category}.{action}"
        cached_value = self._routes.get(key)
        if self.settings.ROUTES_CACHE_ENABLED and cached_value:
            return cached_value
        try:
            value = self.r.hget(self.redis_key, key)
        except Exception as exc:
            logger.exception(
                {
                    "event": "routes.resolve_failed",
                    "redis_key": self.redis_key,
                    "key": key,
                    "error": str(exc),
                    "cached": bool(cached_value),
                }
            )
            return cached_value
        if value:
            self._routes[key] = value
            return value
        if not self.settings.ROUTES_CACHE_ENABLED:
            self._routes.pop(key, None)
        return None

    # ------------------------------------------------------------------
    # ⬅️ __setitem__(): 支持 route_table["tools.add"] = url
    # （用于自动注册 / register API）
    # ------------------------------------------------------------------
    def __setitem__(self, key: str, value: str):
        self._routes[key] = value
        self.r.hset(self.redis_key, key, value)

    # ------------------------------------------------------------------
    # 🔧 add(): 和 __setitem__ 功能重复，但更直观
    # ------------------------------------------------------------------
    def add(self, key: str, value: str):
        self.__setitem__(key, value)

    # ------------------------------------------------------------------
    # 🔎 get(): 用于调试，获取单个 key
    # ------------------------------------------------------------------
    def get(self, key: str) -> str | None:
        return self._routes.get(key)

    # ------------------------------------------------------------------
    # 📋 all(): 列出所有可用路由
    # ------------------------------------------------------------------
    def all(self) -> dict:
        return dict(self._routes)
