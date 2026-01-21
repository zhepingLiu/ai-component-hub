from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from importlib import import_module

import yaml

logger = logging.getLogger("orchestrator")


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

    return {str(k): v for k, v in agents.items() if isinstance(v, dict)}


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
