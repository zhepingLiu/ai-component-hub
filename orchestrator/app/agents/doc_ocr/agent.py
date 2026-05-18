from __future__ import annotations

from typing import Any

from ...core.agent import BaseAgent
from ...core.context import AgentExecutionContext
from ...core.result import AgentExecutionResult
from .callback import send_doc_ocr_callback
from .result_delivery import upload_doc_ocr_result
from .schema import DocOCRReq
from .workflow import build_doc_ocr_client, run_doc_ocr_client, stage_doc_ocr_files


class DocOCRAgent(BaseAgent):
    name = "doc-ocr"
    log_event_prefix = "doc_ocr"

    async def validate_request(self, payload: dict[str, Any]) -> DocOCRReq:
        return DocOCRReq.model_validate(payload)

    async def execute(self, ctx: AgentExecutionContext, req: DocOCRReq) -> AgentExecutionResult:
        cfg = ctx.settings

        try:
            await ctx.tracker.aset_status(
                ctx.request_id,
                status="RUNNING",
                result=None,
                error=None,
                ttl=cfg.JOB_TTL_SEC,
            )
            ctx.logger.info({"event": "doc_ocr.running", "request_id": ctx.request_id, "trace_id": ctx.trace_id})

            staged_files = await stage_doc_ocr_files(ctx=ctx, req=req)
            local_paths = [staged.local_path for staged in staged_files]
            client = build_doc_ocr_client(ctx.agent_config or {})
            agent_res = await run_doc_ocr_client(
                client=client,
                agent_cfg=ctx.agent_config or {},
                req=req,
                local_paths=local_paths,
            )

            if not agent_res.ok:
                error = agent_res.error or "agent upstream error"
                await ctx.tracker.aset_status(
                    ctx.request_id,
                    status="FAILED",
                    result=None,
                    error=error,
                    ttl=cfg.JOB_TTL_SEC,
                )
                ctx.logger.error(
                    {
                        "event": "doc_ocr.failed",
                        "request_id": ctx.request_id,
                        "trace_id": ctx.trace_id,
                        "error": error,
                    }
                )
                return AgentExecutionResult.failed(error)

            result = {
                "staged": [
                    {
                        "url": staged.url,
                        "local_path": staged.local_path,
                        "size_bytes": staged.size_bytes,
                        "sha256": staged.sha256,
                    }
                    for staged in staged_files
                ],
                "agent": agent_res.data,
            }

            await ctx.tracker.aset_status(
                ctx.request_id,
                status="UPLOADING",
                result=result,
                error=None,
                ttl=cfg.JOB_TTL_SEC,
            )
            ctx.logger.info({"event": "doc_ocr.uploading", "request_id": ctx.request_id, "trace_id": ctx.trace_id})

            try:
                result["esb_upload"] = await upload_doc_ocr_result(
                    ctx=ctx,
                    file_refs=list(req.files),
                    agent_data=agent_res.data,
                )
            except Exception as exc:
                error = f"upload_failed: {exc}"
                await ctx.tracker.aset_status(
                    ctx.request_id,
                    status="FAILED",
                    result=None,
                    error=error,
                    ttl=cfg.JOB_TTL_SEC,
                )
                ctx.logger.error(
                    {
                        "event": "doc_ocr.upload_failed",
                        "request_id": ctx.request_id,
                        "trace_id": ctx.trace_id,
                        "error": str(exc),
                    }
                )
                return AgentExecutionResult.failed(error)

            await ctx.tracker.aset_status(
                ctx.request_id,
                status="SUCCEEDED",
                result=result,
                error=None,
                ttl=cfg.JOB_TTL_SEC,
            )
            ctx.logger.info({"event": "doc_ocr.succeeded", "request_id": ctx.request_id, "trace_id": ctx.trace_id})
            return AgentExecutionResult.succeeded(result)
        except Exception as exc:
            error = str(exc)
            await ctx.tracker.aset_status(
                ctx.request_id,
                status="FAILED",
                result=None,
                error=error,
                ttl=cfg.JOB_TTL_SEC,
            )
            ctx.logger.exception({"event": "doc_ocr.unhandled_failed", "request_id": ctx.request_id, "error": error})
            return AgentExecutionResult.failed(error)

    async def postprocess(
        self,
        ctx: AgentExecutionContext,
        req: DocOCRReq,
        result: AgentExecutionResult,
    ) -> AgentExecutionResult:
        callback_url = self._cfg(ctx.agent_config, "callback_url") or ctx.settings.DOC_OCR_CALLBACK_URL
        callback_info = await send_doc_ocr_callback(
            ctx=ctx,
            callback_url=callback_url,
            status=result.status,
            result=result.data,
            error=result.error,
            timeout=ctx.settings.DOC_OCR_CALLBACK_TIMEOUT_SEC,
            max_retries=ctx.settings.DOC_OCR_CALLBACK_MAX_RETRIES,
            base_delay=ctx.settings.DOC_OCR_CALLBACK_BASE_DELAY_SEC,
        )
        if result.status != "SUCCEEDED" or not isinstance(result.data, dict):
            return result

        data = dict(result.data)
        data["callback"] = callback_info
        return AgentExecutionResult.succeeded(data)

    def _cfg(self, agent_cfg: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = agent_cfg.get(key)
            if isinstance(value, str) and value:
                return value
        return ""
