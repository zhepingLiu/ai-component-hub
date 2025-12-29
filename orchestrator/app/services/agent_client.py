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

    def __init__(self, base_url: str = ""):
        self.base_url = base_url.rstrip("/")

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
        示例：假设平台接收 multipart 上传：
          POST {base_url}/agents/doc-ocr/run
        """
        if not self.base_url:
            return AgentResult(ok=False, data={}, error="AGENT_BASE_URL is empty")

        url = f"{self.base_url}/agents/doc-ocr/run"

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                with open(local_file_path, "rb") as f:
                    files = {"file": (Path(local_file_path).name, f, "application/octet-stream")}
                    resp = await client.post(url, data={"options": str(options)}, files=files)
                resp.raise_for_status()
                return AgentResult(ok=True, data=resp.json())
        except Exception as e:
            return AgentResult(ok=False, data={}, error=str(e))
