from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping
from xml.etree import ElementTree as ET

from ...core.context import AgentExecutionContext
from ...services.callbacks import send_callback


_REQUEST_HEADER_KEYS = [
    "ChanlNo",
    "ReqSeqNo",
    "ReqTime",
    "ReqDate",
]

_REQUEST_BODY_KEYS = [
    "BusinessSerlNo",
    "ResultFlag",
    "FilePathAddr",
    "FailReason",
]


def _xml_text(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _append_children(parent: ET.Element, keys: list[str], values: Mapping[str, object]) -> None:
    for key in keys:
        child = ET.SubElement(parent, key)
        child.text = _xml_text(values.get(key, ""))


def build_doc_ocr_callback_xml(
    *,
    request_header: Mapping[str, object],
    request_body: Mapping[str, object],
) -> str:
    ns = "http://schemas.xmlsoap.org/soap/envelope/"
    ET.register_namespace("soapenv", ns)
    envelope = ET.Element(f"{{{ns}}}Envelope")
    body = ET.SubElement(envelope, f"{{{ns}}}Body")
    req = ET.SubElement(body, "Request")
    rh = ET.SubElement(req, "RequestHeader")
    _append_children(rh, _REQUEST_HEADER_KEYS, request_header)
    rb = ET.SubElement(req, "RequestBody")
    _append_children(rb, _REQUEST_BODY_KEYS, request_body)
    envelope_xml = ET.tostring(envelope, encoding="unicode")
    return f"<?xml version='1.0' encoding='utf8'?>{envelope_xml}"


async def send_doc_ocr_callback(
    *,
    ctx: AgentExecutionContext,
    callback_url: str,
    status: str,
    result: dict[str, Any] | None,
    error: str | None,
    timeout: float,
    max_retries: int,
    base_delay: float,
) -> dict[str, Any]:
    raw_headers = ctx.agent_config.get("headers", {}) or {}
    callback_headers = {
        str(k): str(v) for k, v in raw_headers.items() if v is not None and v != ""
    }
    callback_payload = {
        "BusinessSerlNo": ctx.request_id,
        "ResultFlag": 1 if status == "SUCCEEDED" else 2,
        "FilePathAddr": result["esb_upload"]["server_file"] if status == "SUCCEEDED" and result else "",
        "FailReason": error or "",
    }

    channel_value = ""
    if isinstance(raw_headers, dict):
        channel_value = str(raw_headers.get("channel", "")).strip()
    now = datetime.now()
    request_header = {
        "ChanlNo": channel_value,
        "ReqSeqNo": ctx.trace_id or ctx.request_id,
        "ReqTime": now.strftime("%H%M%S"),
        "ReqDate": now.strftime("%Y%m%d"),
    }
    callback_xml_body = build_doc_ocr_callback_xml(
        request_header=request_header,
        request_body=callback_payload,
    )
    return await send_callback(
        callback_url=callback_url,
        payload=callback_payload,
        body=callback_xml_body,
        content_type="text/xml; charset=utf-8",
        headers=callback_headers or None,
        timeout=timeout,
        max_retries=max_retries,
        base_delay=base_delay,
        logger=ctx.logger,
        request_id=ctx.request_id,
        trace_id=ctx.trace_id,
    )
