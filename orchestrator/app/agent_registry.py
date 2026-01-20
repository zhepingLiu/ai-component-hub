from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

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
