import logging
import redis

from .config import Settings, settings as app_settings

logger = logging.getLogger("gateway")

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
        self.reload()   # 启动时加载一次

    # ------------------------------------------------------------------
    # 🔄 reload(): 从 Redis 同步整个路由表
    # ------------------------------------------------------------------
    def reload(self):
        try:
            self._routes = self.r.hgetall(self.redis_key) or {}
        except Exception as exc:
            logger.exception(
                {
                    "event": "routes.reload_failed",
                    "redis_key": self.redis_key,
                    "error": str(exc),
                }
            )
            self._routes = {}

    # ------------------------------------------------------------------
    # 🔍 resolve(): 根据 category + action 得到 URL
    # ------------------------------------------------------------------
    def resolve(self, category: str, action: str) -> str | None:
        return self._routes.get(f"{category}.{action}")

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
