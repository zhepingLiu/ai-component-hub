from __future__ import annotations

import unittest
from unittest.mock import patch

from app.agents.generic_agent.schema import GenericAgentReq
from app.agents.generic_agent.workflow import build_generic_agent_client


class GenericAgentSchemaWorkflowTests(unittest.TestCase):
    def test_files_are_optional(self) -> None:
        req = GenericAgentReq.model_validate({"inputs": {"a": 1}, "options": {"stream": False}})

        self.assertEqual(req.files, [])
        self.assertEqual(req.inputs, {"a": 1})
        self.assertEqual(req.options, {"stream": False})

    def test_file_accepts_url_or_filename(self) -> None:
        by_url = GenericAgentReq.model_validate({"files": [{"url": "http://files/a.pdf"}]})
        by_filename = GenericAgentReq.model_validate({"files": [{"filename": "/tmp/a.pdf"}]})

        self.assertEqual(by_url.files[0].url, "http://files/a.pdf")
        self.assertEqual(by_filename.files[0].filename, "/tmp/a.pdf")

    def test_inputs_and_options_allow_json_objects(self) -> None:
        req = GenericAgentReq.model_validate(
            {
                "inputs": {"nested": {"value": 1}, "items": [1, 2]},
                "options": {"stream": False, "temperature": 0},
            }
        )

        self.assertEqual(req.inputs["nested"], {"value": 1})
        self.assertEqual(req.options["temperature"], 0)

    def test_build_client_reads_query_and_environment_fallback(self) -> None:
        with patch("app.agents.generic_agent.workflow.settings.AB_ACCESS_KEY", "env-ak"), patch(
            "app.agents.generic_agent.workflow.settings.AB_SECRET_KEY",
            "env-sk",
        ):
            client = build_generic_agent_client(
                {
                    "base_url": "http://ab",
                    "api_key": "api-key",
                    "query": {"app_id": "app-1", "department_id": "dept-1"},
                }
            )

        self.assertEqual(client.base_url, "http://ab")
        self.assertEqual(client.authorization, "Bearer api-key")
        self.assertEqual(client.access_key, "env-ak")
        self.assertEqual(client.secret_key, "env-sk")
        self.assertEqual(client.app_id, "app-1")
        self.assertEqual(client.department_id, "dept-1")


if __name__ == "__main__":
    unittest.main()
