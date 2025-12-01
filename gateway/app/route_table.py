import redis
import json

class RouteTable:
    def __init__(self):
        # decode_responses=True 让读到的是 str 而不是 bytes
        self.r = redis.Redis(host="redis", port=6379, db=0, decode_responses=True)
        self._routes = {}
        self.reload()   # 启动时加载一次

    # ------------------------------------------------------------------
    # 🔄 reload(): 从 Redis 同步整个路由表
    # ------------------------------------------------------------------
    def reload(self):
        self._routes = self.r.hgetall("routes") or {}

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
        self.r.hset("routes", key, value)

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

