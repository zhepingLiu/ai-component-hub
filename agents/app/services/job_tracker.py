from __future__ import annotations

import json
import uuid
from typing import Any

from ..config import settings


class JobTracker:
    """Manage per-request job keys, idempotency locks, and status storage in Redis."""

    def __init__(self, redis_client, key_prefix: str | None = None):
        self.r = redis_client
        prefix = key_prefix or settings.REDIS_KEY_PREFIX
        # Avoid double colon when caller already provides trailing ':'
        self.key_prefix = prefix.rstrip(":")

    def _key(self, kind: str, request_id: str) -> str:
        return f"{self.key_prefix}:{kind}:{request_id}"

    def ensure_request_id(self, request_id: str | None) -> str:
        return request_id or str(uuid.uuid4())

    def get_job(self, request_id: str) -> tuple[str, dict[str, Any] | None]:
        job_key = self._key("job", request_id)
        raw = self.r.get(job_key)
        return job_key, json.loads(raw) if raw else None

    def acquire_lock(self, request_id: str, ttl: int) -> tuple[str | None, str]:
        lock_key = self._key("lock", request_id)
        token = str(uuid.uuid4())
        got_lock = self.r.set(lock_key, token, nx=True, ex=ttl)
        return (token if got_lock else None), lock_key

    def release_lock(self, request_id: str, token: str) -> None:
        lock_key = self._key("lock", request_id)
        try:
            cur = self.r.get(lock_key)
            if cur == token:
                self.r.delete(lock_key)
        except Exception:
            pass

    def set_status(self, request_id: str, status: str, *, result: Any = None, error: str | None = None, ttl: int = 0) -> tuple[str, dict[str, Any]]:
        job_key = self._key("job", request_id)
        payload = {"status": status, "result": result, "error": error}
        self.r.set(job_key, json.dumps(payload), ex=ttl or None)
        return job_key, payload
