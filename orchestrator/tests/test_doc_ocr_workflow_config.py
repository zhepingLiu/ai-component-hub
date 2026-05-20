from __future__ import annotations

import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

schemas_common = types.ModuleType("app.schemas.common")


class AgentResult:
    def __init__(self, ok: bool, data: dict, error: str | None = None):
        self.ok = ok
        self.data = data
        self.error = error


schemas_common.AgentResult = AgentResult
sys.modules.setdefault("app.schemas.common", schemas_common)

core_context = types.ModuleType("app.core.context")
core_context.AgentExecutionContext = object
sys.modules.setdefault("app.core.context", core_context)

app_config = types.ModuleType("app.config")
app_config.settings = SimpleNamespace(AB_ACCESS_KEY="", AB_SECRET_KEY="")
sys.modules.setdefault("app.config", app_config)

file_stage = types.ModuleType("app.services.file_stage")
file_stage.StagedFile = object
file_stage.download_to_staging = None
sys.modules.setdefault("app.services.file_stage", file_stage)

doc_ocr_schema = types.ModuleType("app.agents.doc_ocr.schema")
doc_ocr_schema.DocOCRReq = object
sys.modules.setdefault("app.agents.doc_ocr.schema", doc_ocr_schema)

from app.agents.doc_ocr.workflow import build_doc_ocr_client


class DocOCRWorkflowConfigTests(unittest.TestCase):
    def test_access_key_and_secret_key_fall_back_to_environment_settings(self) -> None:
        with patch("app.agents.doc_ocr.workflow.settings.AB_ACCESS_KEY", "env-ak"), patch(
            "app.agents.doc_ocr.workflow.settings.AB_SECRET_KEY",
            "env-sk",
        ):
            client = build_doc_ocr_client({"api_key": "api-key", "query": {"app_id": "app-1"}})

        self.assertEqual(client.access_key, "env-ak")
        self.assertEqual(client.secret_key, "env-sk")

    def test_agent_config_overrides_environment_settings(self) -> None:
        with patch("app.agents.doc_ocr.workflow.settings.AB_ACCESS_KEY", "env-ak"), patch(
            "app.agents.doc_ocr.workflow.settings.AB_SECRET_KEY",
            "env-sk",
        ):
            client = build_doc_ocr_client(
                {
                    "api_key": "api-key",
                    "access_key": "cfg-ak",
                    "secret_key": "cfg-sk",
                    "query": {"app_id": "app-1"},
                }
            )

        self.assertEqual(client.access_key, "cfg-ak")
        self.assertEqual(client.secret_key, "cfg-sk")


if __name__ == "__main__":
    unittest.main()
