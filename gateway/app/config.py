from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    API_PREFIX: str = "/api"
    GW_API_KEY: str | None = None  # 开发期可留空
    REQUEST_TIMEOUT_SEC: float = 15.0
    RETRIES: int = 1               # MVP 先 1 次重试
    ENABLE_METRICS: bool = True
    ENABLE_RATE_LIMIT: bool = False
    TRUSTED_PROXIES: str = ""      # 内网可不配
    ROUTE_FILE: str = "/app/routes.yaml" # 路由文件路径
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str | None = None
    REDIS_KEY_PREFIX: str = "aihub:gateway"

    class Config:
        env_file = ".env"

settings = Settings()
