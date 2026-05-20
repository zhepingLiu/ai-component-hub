from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any


def _body_to_text(body: Any) -> str:
    if body is None:
        return ""
    if isinstance(body, bytes):
        return body.decode("utf-8")
    if isinstance(body, str):
        return body
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def build_ab_token_headers(
    *,
    access_key: str,
    secret_key: str,
    content_type: str,
    body: Any = None,
    request_id: str | None = None,
    sign_time: str | None = None,
) -> dict[str, str]:
    """
    Build AB service-signature headers.

    AB only signs the request body when the request is JSON and has a body.
    Multipart uploads therefore sign an empty body.
    """
    request_id = request_id or uuid.uuid4().hex
    sign_time = sign_time or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))
    body_text = _body_to_text(body) if "application/json" in (content_type or "").lower() and body else ""
    token_source = body_text + secret_key + request_id + sign_time
    token = hashlib.sha256(token_source.encode("utf-8")).hexdigest()

    return {
        "X-Bce-Request-ID": request_id,
        "Sign-Time": sign_time,
        "Access-Key": access_key,
        "Token": token,
    }
