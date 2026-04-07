from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from importlib import import_module

import yaml
from .config import settings

logger = logging.getLogger("orchestrator")


def _apply_env_overrides(agents: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {name: dict(cfg) for name, cfg in agents.items()}

    doc_ocr = result.get("doc-ocr")
    if not isinstance(doc_ocr, dict):
        return result

    query = doc_ocr.get("query")
    query_map = dict(query) if isinstance(query, dict) else {}

    headers = doc_ocr.get("headers")
    headers_map = dict(headers) if isinstance(headers, dict) else {}

    if settings.DOC_OCR_BASE_URL:
        doc_ocr["base_url"] = settings.DOC_OCR_BASE_URL
    if settings.DOC_OCR_CALLBACK_URL:
        doc_ocr["callback_url"] = settings.DOC_OCR_CALLBACK_URL
    if settings.DOC_OCR_CONVERSATION_URL:
        doc_ocr["conversation_url"] = settings.DOC_OCR_CONVERSATION_URL
    if settings.DOC_OCR_UPLOAD_URL:
        doc_ocr["upload_url"] = settings.DOC_OCR_UPLOAD_URL
    if settings.DOC_OCR_RUN_URL:
        doc_ocr["run_url"] = settings.DOC_OCR_RUN_URL
    if settings.DOC_OCR_APP_ID:
        doc_ocr["app_id"] = settings.DOC_OCR_APP_ID
        query_map["app_id"] = settings.DOC_OCR_APP_ID
    if settings.DOC_OCR_DEPARTMENT_ID:
        doc_ocr["department_id"] = settings.DOC_OCR_DEPARTMENT_ID
        query_map["department_id"] = settings.DOC_OCR_DEPARTMENT_ID

    authorization = settings.DOC_OCR_AUTHORIZATION or settings.DOC_OCR_PRIVATE_KEY
    if authorization:
        doc_ocr["authorization"] = authorization
        headers_map["X-Private-Key"] = authorization

    if settings.DOC_OCR_CHANNEL:
        headers_map["channel"] = settings.DOC_OCR_CHANNEL

    if settings.DOC_OCR_USE_REAL:
        doc_ocr["use_real"] = True

    if query_map:
        doc_ocr["query"] = query_map
    if headers_map:
        doc_ocr["headers"] = headers_map

    return result


def load_agent_configs(path: str) -> dict[str, dict[str, Any]]:
    config_path = Path(path)
    if not config_path.exists():
        logger.warning({"event": "agents.config_missing", "path": str(config_path)})
        return {}

    with config_path.open("r") as f:
        data = yaml.safe_load(f) or {}

    agents = data.get("agents", {}) if isinstance(data, dict) else {}
    if not isinstance(agents, dict):
        logger.warning({"event": "agents.config_invalid", "path": str(config_path)})
        return {}

    parsed = {str(k): v for k, v in agents.items() if isinstance(v, dict)}
    return _apply_env_overrides(parsed)


def normalize_handler_name(name: str) -> str:
    return name.replace("-", "_")


def get_handler_name(agent_name: str, agent_cfg: dict[str, Any]) -> str:
    handler = agent_cfg.get("handler")
    if isinstance(handler, str) and handler:
        return handler
    return normalize_handler_name(agent_name)


def load_agent_handler(handler_name: str):
    module = import_module(f"app.agents.{handler_name}.handler")
    if not hasattr(module, "run"):
        raise AttributeError(f"Agent handler missing run(): {handler_name}")
    return module


def build_gateway_entries(agents: dict[str, dict[str, Any]], base_url: str, category: str) -> list[dict[str, str]]:
    entries = []
    base = base_url.rstrip("/")
    for name, cfg in agents.items():
        if cfg.get("enable_register") is False:
            continue
        action = cfg.get("gateway_action") or name
        if not isinstance(action, str) or not action:
            action = name
        route_path = cfg.get("route_path") or f"/agents/{name}"
        if not isinstance(route_path, str) or not route_path:
            route_path = f"/agents/{name}"
        if not route_path.startswith("/"):
            route_path = f"/{route_path}"
        entries.append(
            {
                "category": str(category),
                "action": str(action),
                "url": f"{base}{route_path}",
            }
        )
    return entries
