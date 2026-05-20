from __future__ import annotations

import logging
import json
from pathlib import Path
from typing import Any, Dict

import httpx

from ...schemas.common import AgentResult
from ...services.ab_token import build_ab_token_headers

logger = logging.getLogger("orchestrator.doc_ocr.client")


class DocOCRClient:
    """
    调用后端智能体平台的 client。
    一期先 stub（返回假结果），后续对接真实平台时，只需要替换 run_doc_ocr() 的实现即可。
    """

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
        mock_result_path: str = "",
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
        self.mock_result_path = mock_result_path

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

    def _load_mock_result(self) -> AgentResult:
        mock_path = Path(self.mock_result_path).expanduser()
        if not mock_path.is_absolute():
            mock_path = (Path.cwd() / mock_path).resolve()

        try:
            data = json.loads(mock_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            logger.error({"event": "doc_ocr_mock_result.missing", "path": str(mock_path)})
            return AgentResult(ok=False, data={}, error=f"Mock result file not found: {mock_path}")
        except json.JSONDecodeError as exc:
            logger.error({"event": "doc_ocr_mock_result.invalid_json", "path": str(mock_path), "error": str(exc)})
            return AgentResult(ok=False, data={}, error=f"Mock result file is not valid JSON: {mock_path}")
        except Exception as exc:
            logger.exception({"event": "doc_ocr_mock_result.load_failed", "path": str(mock_path), "error": str(exc)})
            return AgentResult(ok=False, data={}, error=f"Failed to load mock result file: {exc}")

        if not isinstance(data, dict):
            logger.error({"event": "doc_ocr_mock_result.invalid_shape", "path": str(mock_path)})
            return AgentResult(ok=False, data={}, error="Mock result file must contain a JSON object")

        logger.info({"event": "doc_ocr_mock_result.loaded", "path": str(mock_path)})
        return AgentResult(ok=True, data=data)

    async def run_doc_ocr_mock(self, *, local_file_path: str, options: Dict[str, Any]) -> AgentResult:
        return self._load_mock_result()

    async def run_doc_ocr_mock_many(self, *, local_file_paths: list[str], options: Dict[str, Any]) -> AgentResult:
        return self._load_mock_result()

    async def run_doc_ocr(self, *, local_file_path: str, options: Dict[str, Any]) -> AgentResult:
        # -------------------------
        # STUB：先返回假数据
        # -------------------------
        p = Path(local_file_path)

        return AgentResult(
            ok=True,
            data={
                "agent": "doc-ocr-agent",
                "stub": True,
                "filename": p.name,
                "size_bytes": p.stat().st_size if p.exists() else None,
                "text": "这是一个stub的OCR结果（后续对接真实智能体平台后会替换）",
                "options": options,
            },
        )

    async def run_doc_ocr_many(self, *, local_file_paths: list[str], options: Dict[str, Any]) -> AgentResult:
        files = []
        for local_file_path in local_file_paths:
            p = Path(local_file_path)
            files.append(
                {
                    "filename": p.name,
                    "size_bytes": p.stat().st_size if p.exists() else None,
                }
            )

        return AgentResult(
            ok=True,
            data={
                "agent": "doc-ocr-agent",
                "stub": True,
                "files": files,
                "options": options,
            },
        )

    # 真实对接示例（你们后续需要时再启用）
    async def run_doc_ocr_real(self, *, local_file_path: str, options: Dict[str, Any]) -> AgentResult:
        """
        真实对接：创建会话 -> 上传文件 -> 触发运行
        参数均可变，留空时返回错误，由上层注入。
        """
        conversation_url = self.conversation_url or self._default_url("/api/ai_apaas/v1/app/conversation")
        upload_url = self.upload_url or self._default_url("/api/ai_apaas/v1/app/conversation/file/upload")
        run_url = self.run_url or self._default_url("/api/ai_apaas/v1/app/conversation/runs")
        logger.info(
            {
                "event": "doc_ocr_real.config",
                "conversation_url": conversation_url,
                "upload_url": upload_url,
                "run_url": run_url,
                "has_authorization": bool(self.authorization),
                "has_access_key": bool(self.access_key),
                "has_secret_key": bool(self.secret_key),
                "has_app_id": bool(self.app_id),
            }
        )

        if not conversation_url or not upload_url or not run_url:
            return AgentResult(ok=False, data={}, error="Agent URLs are not configured")
        if not self.authorization:
            return AgentResult(ok=False, data={}, error="Agent authorization is not configured")
        if not self.access_key or not self.secret_key:
            return AgentResult(ok=False, data={}, error="Agent access_key/secret_key is not configured")
        if not self.app_id:
            return AgentResult(ok=False, data={}, error="Agent app_id is not configured")

        headers_json = self._json_headers()

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                # 1) 创建会话
                conv_payload: Dict[str, Any] = {"app_id": self.app_id}
                if self.department_id:
                    conv_payload["department_id"] = self.department_id
                conv_resp = await client.post(conversation_url, json=conv_payload, headers=headers_json)
                conv_resp.raise_for_status()
                conv_id = conv_resp.json().get("conversation_id")
                if not conv_id:
                    return AgentResult(ok=False, data={}, error="Missing conversation_id in response")
                logger.info({"event": "doc_ocr_real.conversation_ok", "conversation_id": conv_id})

                # 2) 上传文件
                with open(local_file_path, "rb") as f:
                    files = {"file": f}
                    form_data: Dict[str, Any] = {
                        "app_id": str(self.app_id),
                        "conversation_id": str(conv_id),
                    }
                    if self.department_id:
                        form_data["department_id"] = str(self.department_id)
                    upload_resp = await client.post(upload_url, headers=self._upload_headers(), data=form_data, files=files)
                upload_resp.raise_for_status()
                file_id = upload_resp.json().get("id")
                if not file_id:
                    return AgentResult(ok=False, data={}, error="Missing file id in upload response")
                logger.info({"event": "doc_ocr_real.upload_ok", "file_id": file_id})

                # 3) 触发运行
                run_payload: Dict[str, Any] = {
                    "app_id": self.app_id,
                    "conversation_id": conv_id,
                    "file_ids": [file_id],
                }
                if self.department_id:
                    run_payload["department_id"] = self.department_id
                if options:
                    run_payload.update(options)
                run_resp = await client.post(run_url, headers=headers_json, json=run_payload)
                run_resp.raise_for_status()
                try:
                    data = run_resp.json()
                except ValueError:
                    data = {"raw": run_resp.text}
                logger.info({"event": "doc_ocr_real.run_ok"})
                return AgentResult(ok=True, data=data)
        except Exception as e:
            logger.exception({"event": "doc_ocr_real.failed", "error": str(e)})
            return AgentResult(ok=False, data={}, error=str(e))

    async def run_doc_ocr_real_many(self, *, local_file_paths: list[str], options: Dict[str, Any]) -> AgentResult:
        """
        真实对接：创建会话 -> 逐个上传文件 -> 触发运行（携带多个 file_ids）
        参数均可变，留空时返回错误，由上层注入。
        """
        conversation_url = self.conversation_url or self._default_url("/api/ai_apaas/v1/app/conversation")
        upload_url = self.upload_url or self._default_url("/api/ai_apaas/v1/app/conversation/file/upload")
        run_url = self.run_url or self._default_url("/api/ai_apaas/v1/app/conversation/runs")
        logger.info(
            {
                "event": "doc_ocr_real_many.config",
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

        if not conversation_url or not upload_url or not run_url:
            return AgentResult(ok=False, data={}, error="Agent URLs are not configured")
        if not self.authorization:
            return AgentResult(ok=False, data={}, error="Agent authorization is not configured")
        if not self.access_key or not self.secret_key:
            return AgentResult(ok=False, data={}, error="Agent access_key/secret_key is not configured")
        if not self.app_id:
            return AgentResult(ok=False, data={}, error="Agent app_id is not configured")
        if not local_file_paths:
            return AgentResult(ok=False, data={}, error="No local files provided")

        headers_json = self._json_headers()

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                conv_payload: Dict[str, Any] = {"app_id": self.app_id}
                if self.department_id:
                    conv_payload["department_id"] = self.department_id
                conv_resp = await client.post(conversation_url, json=conv_payload, headers=headers_json)
                conv_resp.raise_for_status()
                conv_id = conv_resp.json().get("conversation_id")
                if not conv_id:
                    return AgentResult(ok=False, data={}, error="Missing conversation_id in response")
                logger.info({"event": "doc_ocr_real_many.conversation_ok", "conversation_id": conv_id})

                file_ids: list[str] = []
                for local_file_path in local_file_paths:
                    with open(local_file_path, "rb") as f:
                        files = {"file": f}
                        form_data: Dict[str, Any] = {
                            "app_id": str(self.app_id),
                            "conversation_id": str(conv_id),
                        }
                        if self.department_id:
                            form_data["department_id"] = str(self.department_id)
                        upload_resp = await client.post(upload_url, headers=self._upload_headers(), data=form_data, files=files)
                    upload_resp.raise_for_status()
                    file_id = upload_resp.json().get("id")
                    if not file_id:
                        return AgentResult(ok=False, data={}, error="Missing file id in upload response")
                    file_ids.append(file_id)
                    logger.info({"event": "doc_ocr_real_many.upload_ok", "file_id": file_id})

                run_payload: Dict[str, Any] = {
                    "app_id": self.app_id,
                    "conversation_id": conv_id,
                    "file_ids": file_ids,
                }
                if self.department_id:
                    run_payload["department_id"] = self.department_id
                if options:
                    run_payload.update(options)
                run_resp = await client.post(run_url, headers=headers_json, json=run_payload)
                run_resp.raise_for_status()
                try:
                    data = run_resp.json()
                except ValueError:
                    data = {"raw": run_resp.text}
                logger.info({"event": "doc_ocr_real_many.run_ok"})
                return AgentResult(ok=True, data=data)
        except Exception as e:
            logger.exception({"event": "doc_ocr_real_many.failed", "error": str(e)})
            return AgentResult(ok=False, data={}, error=str(e))
