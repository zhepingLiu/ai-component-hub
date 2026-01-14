from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ..redis_client import get_redis
from ..schemas.route_schemas import StdResp
from ..services.agent_config_store import list_agent_configs, get_agent_config, mask_config


router = APIRouter()
logger = logging.getLogger("agents")


@router.get("/configs")
def list_configs(request: Request):
    r = get_redis(request.app)
    configs = {name: mask_config(cfg) for name, cfg in list_agent_configs(r).items()}
    return StdResp(code=0, data=configs).model_dump()


@router.get("/configs/{name}")
def get_config(name: str, request: Request):
    r = get_redis(request.app)
    config = get_agent_config(r, name)
    if not config:
        logger.warning({"event": "agent_config.missing", "name": name})
        raise HTTPException(status_code=404, detail="agent_config_not_found")
    return StdResp(code=0, data=mask_config(config)).model_dump()
