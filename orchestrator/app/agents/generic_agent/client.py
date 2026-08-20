from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from ...schemas.common import AgentResult
from ...services.ab_token import build_ab_token_headers

logger = logging.getLogger("orchestrator.generic_agent.client")


class GenericAgentClient:
    def __init__(
        self,
        base_url: str = "",
        *,
        conversation_url: str = "",
        upload_url: str = "",
        run_url: str = "",
        authorization: str = "",
        access_key: str = "",
        secret_key: str = "",
        x_authorization: str = "",
        app_id: str = "",
        department_id: str = "",
        timeout: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.conversation_url = conversation_url
        self.upload_url = upload_url
        self.run_url = run_url
        self.authorization = self._normalize_authorization(authorization)
        self.access_key = access_key
        self.secret_key = secret_key
        self.x_authorization = x_authorization
        self.app_id = app_id
        self.department_id = department_id
        self.timeout = timeout

    @staticmethod
    def _normalize_authorization(value: str) -> str:
        value = (value or "").strip()
        if not value:
            return ""
        if value.lower().startswith("bearer "):
            return value
        return f"Bearer {value}"

    def _default_url(self, path: str) -> str:
        base = self.base_url.rstrip("/")
        return f"{base}{path}" if base else path

    def _json_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": self.authorization,
        }
        if self.x_authorization:
            headers["X-Authorization"] = self.x_authorization
        return headers

    def _upload_headers(self) -> dict[str, str]:
        headers = build_ab_token_headers(
            access_key=self.access_key,
            secret_key=self.secret_key,
            content_type="multipart/form-data",
        )
        if self.x_authorization:
            headers["X-Authorization"] = self.x_authorization
        return headers

    def _resolve_urls(self) -> tuple[str, str, str]:
        conversation_url = self.conversation_url or self._default_url("/api/ai_apaas/v1/app/conversation")
        upload_url = self.upload_url or self._default_url("/api/ai_apaas/v1/app/conversation/file/upload")
        run_url = self.run_url or self._default_url("/api/ai_apaas/v1/app/conversation/runs")
        return conversation_url, upload_url, run_url

    def _validate_config(self, *, needs_upload: bool) -> str | None:
        conversation_url, upload_url, run_url = self._resolve_urls()
        if not conversation_url or not run_url or (needs_upload and not upload_url):
            return "Agent URLs are not configured"
        if not self.authorization:
            return "Agent api_key is not configured"
        if needs_upload and (not self.access_key or not self.secret_key):
            return "Agent access_key/secret_key is not configured"
        if not self.app_id:
            return "Agent app_id is not configured"
        return None

    async def run(
        self,
        *,
        local_file_paths: list[str],
        inputs: dict[str, Any],
        options: dict[str, Any],
    ) -> AgentResult:
        needs_upload = bool(local_file_paths)
        config_error = self._validate_config(needs_upload=needs_upload)
        if config_error:
            return AgentResult(ok=False, data={}, error=config_error)

        conversation_url, upload_url, run_url = self._resolve_urls()
        logger.info(
            {
                "event": "generic_agent.config",
                "conversation_url": conversation_url,
                "upload_url": upload_url,
                "run_url": run_url,
                "has_authorization": bool(self.authorization),
                "has_access_key": bool(self.access_key),
                "has_secret_key": bool(self.secret_key),
                "has_app_id": bool(self.app_id),
                "file_count": len(local_file_paths or []),
            }
        )

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                conv_payload: dict[str, Any] = {"app_id": self.app_id}
                if self.department_id:
                    conv_payload["department_id"] = self.department_id
                conv_resp = await client.post(conversation_url, json=conv_payload, headers=self._json_headers())
                conv_resp.raise_for_status()
                conv_id = conv_resp.json().get("conversation_id")
                if not conv_id:
                    return AgentResult(ok=False, data={}, error="Missing conversation_id in response")
                logger.info({"event": "generic_agent.conversation_ok", "conversation_id": conv_id})

                file_ids: list[str] = []
                for local_file_path in local_file_paths:
                    path = Path(local_file_path)
                    with path.open("rb") as f:
                        form_data: dict[str, Any] = {
                            "app_id": str(self.app_id),
                            "conversation_id": str(conv_id),
                        }
                        if self.department_id:
                            form_data["department_id"] = str(self.department_id)
                        upload_resp = await client.post(
                            upload_url,
                            headers=self._upload_headers(),
                            data=form_data,
                            files={"file": (path.name, f)},
                        )
                    upload_resp.raise_for_status()
                    file_id = upload_resp.json().get("id")
                    if not file_id:
                        return AgentResult(ok=False, data={}, error="Missing file id in upload response")
                    file_ids.append(file_id)
                    logger.info({"event": "generic_agent.upload_ok", "file_id": file_id})

                run_payload: dict[str, Any] = {
                    "app_id": self.app_id,
                    "conversation_id": conv_id,
                }
                if self.department_id:
                    run_payload["department_id"] = self.department_id
                if file_ids:
                    run_payload["file_ids"] = file_ids
                if inputs:
                    run_payload["inputs"] = inputs
                if options:
                    run_payload.update(options)

                run_resp = await client.post(run_url, headers=self._json_headers(), json=run_payload)
                run_resp.raise_for_status()
                try:
                    data = run_resp.json()
                except ValueError:
                    data = {"raw": run_resp.text}
                logger.info({"event": "generic_agent.run_ok"})
                return AgentResult(ok=True, data=data)
        except Exception as exc:
            logger.exception({"event": "generic_agent.failed", "error": str(exc)})
            return AgentResult(ok=False, data={}, error=str(exc))

    def __repr__(self) -> str:
        safe = {
            "base_url": self.base_url,
            "conversation_url": self.conversation_url,
            "upload_url": self.upload_url,
            "run_url": self.run_url,
            "app_id": self.app_id,
            "department_id": self.department_id,
            "timeout": self.timeout,
        }
        return f"{self.__class__.__name__}({json.dumps(safe, ensure_ascii=False)})"
