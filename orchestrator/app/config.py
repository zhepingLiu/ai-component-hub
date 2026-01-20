from __future__ import annotations

import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # Redis
    REDIS_HOST: str = os.getenv("REDIS_HOST", "redis")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))
    REDIS_PASSWORD: str | None = os.getenv("REDIS_PASSWORD") or None
    REDIS_REQUIRED: bool = os.getenv("REDIS_REQUIRED", "true").lower() == "true"

    # Namespace（避免不同系统/环境 key 冲突）
    REDIS_KEY_PREFIX: str = os.getenv("REDIS_KEY_PREFIX", "aihub:orchestrator")

    # 任务/幂等相关默认 TTL（秒）
    IDEMPOTENCY_TTL_SEC: int = int(os.getenv("IDEMPOTENCY_TTL_SEC", "3600"))  # 1h
    JOB_TTL_SEC: int = int(os.getenv("JOB_TTL_SEC", "86400"))  # 24h

    # staging 目录（必须挂载外部卷）
    STAGING_DIR: str = os.getenv("STAGING_DIR", "/app/data/staging")

    # ESB service base URL（同 docker-compose 内服务名）
    ESB_BASE_URL: str = os.getenv("ESB_BASE_URL", "http://esb:7002")

    # Logging
    LOG_DIR: str = os.getenv("LOG_DIR", "/app/data/logs")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_RETENTION_DAYS: int = int(os.getenv("LOG_RETENTION_DAYS", "10"))

    # Agent proxy
    REQUEST_TIMEOUT_SEC: float = float(os.getenv("REQUEST_TIMEOUT_SEC", "15.0"))

    # Agent config
    AGENT_CONFIG_FILE: str = os.getenv("AGENT_CONFIG_FILE", "/app/agents.yaml")


settings = Settings()
