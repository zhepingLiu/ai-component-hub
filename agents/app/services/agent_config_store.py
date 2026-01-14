from __future__ import annotations

import json
import logging
from typing import Any, Dict

from ..config import settings


logger = logging.getLogger("agents")


def _config_key() -> str:
    return f"{settings.REDIS_KEY_PREFIX}:agent_configs"


def _parse_config(raw: str | None, name: str) -> Dict[str, Any] | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        logger.warning({"event": "agent_config.invalid", "name": name, "reason": "not_dict"})
    except Exception as e:
        logger.warning({"event": "agent_config.invalid", "name": name, "error": str(e)})
    return None


def get_agent_config(redis_client, name: str) -> Dict[str, Any] | None:
    raw = redis_client.hget(_config_key(), name)
    return _parse_config(raw, name)


def list_agent_configs(redis_client) -> Dict[str, Dict[str, Any]]:
    raw_map = redis_client.hgetall(_config_key()) or {}
    configs: Dict[str, Dict[str, Any]] = {}
    for name, raw in raw_map.items():
        parsed = _parse_config(raw, name)
        if parsed is not None:
            configs[name] = parsed
    return configs


def mask_config(config: Dict[str, Any]) -> Dict[str, Any]:
    masked = dict(config)
    value = masked.get("authorization")
    if isinstance(value, str) and value:
        if len(value) <= 8:
            masked["authorization"] = "****"
        else:
            masked["authorization"] = f"{value[:4]}****{value[-4:]}"
    return masked
