from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

@dataclass
class AgentResult:
    ok: bool
    data: Dict[str, Any]
    error: Optional[str] = None


class AgentClient:
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
        app_id: str = "",
        department_id: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.conversation_url = conversation_url
        self.upload_url = upload_url
        self.run_url = run_url
        self.authorization = authorization
        self.app_id = app_id
        self.department_id = department_id

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

    # 真实对接示例（你们后续需要时再启用）
    async def run_doc_ocr_real(self, *, local_file_path: str, options: Dict[str, Any]) -> AgentResult:
        """
        真实对接：创建会话 -> 上传文件 -> 触发运行
        参数均可变，留空时返回错误，由上层注入。
        """
        conversation_url = self.conversation_url or f"{self.base_url}/v2/app/conversation"
        upload_url = self.upload_url or f"{self.base_url}/v2/app/conversation/file/upload"
        run_url = self.run_url or f"{self.base_url}/v2/app/conversation/runs"

        if not conversation_url or not upload_url or not run_url:
            return AgentResult(ok=False, data={}, error="Agent URLs are not configured")
        if not self.authorization:
            return AgentResult(ok=False, data={}, error="Agent authorization is not configured")
        if not self.app_id:
            return AgentResult(ok=False, data={}, error="Agent app_id is not configured")

        headers_json = {
            "Content-Type": "application/json",
            "Authorization": self.authorization,
        }
        headers_auth = {"Authorization": self.authorization}

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

                # 2) 上传文件
                with open(local_file_path, "rb") as f:
                    files = {"file": (Path(local_file_path).name, f)}
                    form_data: Dict[str, Any] = {
                        "app_id": self.app_id,
                        "conversation_id": conv_id,
                    }
                    if self.department_id:
                        form_data["department_id"] = self.department_id
                    upload_resp = await client.post(upload_url, headers=headers_auth, data=form_data, files=files)
                upload_resp.raise_for_status()
                file_id = upload_resp.json().get("id")
                if not file_id:
                    return AgentResult(ok=False, data={}, error="Missing file id in upload response")

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
                return AgentResult(ok=True, data=data)
        except Exception as e:
            return AgentResult(ok=False, data={}, error=str(e))
