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

    # staging 目录（容器内可写路径）
    STAGING_DIR: str = os.getenv("STAGING_DIR", "/app/data/staging")

    # ESB service base URL（同 docker-compose 内服务名）
    ESB_BASE_URL: str = os.getenv("ESB_BASE_URL", "http://esb:7002")

    # 文件服务器上传 appSource 字段
    FILE_SERVER_APPSOURCE: str = os.getenv("FILE_SERVER_APPSOURCE", "CQRCB_ESBFILE_SOURCE")

    # Logging
    LOG_DIR: str = os.getenv("LOG_DIR", "/app/data/logs")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_RETENTION_DAYS: int = int(os.getenv("LOG_RETENTION_DAYS", "10"))

    # Agent proxy
    REQUEST_TIMEOUT_SEC: float = float(os.getenv("REQUEST_TIMEOUT_SEC", "15.0"))
    STAGING_DOWNLOAD_TIMEOUT_SEC: float = float(os.getenv("STAGING_DOWNLOAD_TIMEOUT_SEC", "120.0"))
    ESB_UPLOAD_TIMEOUT_SEC: float = float(os.getenv("ESB_UPLOAD_TIMEOUT_SEC", "60.0"))
    DOC_OCR_CALLBACK_URL: str = os.getenv("DOC_OCR_CALLBACK_URL", "")
    DOC_OCR_CALLBACK_TIMEOUT_SEC: float = float(os.getenv("DOC_OCR_CALLBACK_TIMEOUT_SEC", "10.0"))
    DOC_OCR_CALLBACK_MAX_RETRIES: int = int(os.getenv("DOC_OCR_CALLBACK_MAX_RETRIES", "5"))
    DOC_OCR_CALLBACK_BASE_DELAY_SEC: float = float(os.getenv("DOC_OCR_CALLBACK_BASE_DELAY_SEC", "1.0"))

    # Agent config
    AGENT_CONFIG_FILE: str = os.getenv("AGENT_CONFIG_FILE", "/app/agents.yaml")

    # Gateway registration
    GATEWAY_URL: str = os.getenv("GATEWAY_URL", "http://gateway:8000")
    GW_API_KEY: str | None = os.getenv("GW_API_KEY") or None
    ORCHESTRATOR_BASE_URL: str = os.getenv("ORCHESTRATOR_BASE_URL", "http://orchestrator:7010")
    GATEWAY_CATEGORY: str = os.getenv("GATEWAY_CATEGORY", "agents")
    REGISTER_RETRY_SECONDS: int = int(os.getenv("REGISTER_RETRY_SECONDS", "2"))
    REGISTER_MAX_ATTEMPTS: int = int(os.getenv("REGISTER_MAX_ATTEMPTS", "15"))


settings = Settings()
