from __future__ import annotations

import sys
import types
import unittest

core_context = types.ModuleType("app.core.context")
core_context.AgentExecutionContext = object
sys.modules.setdefault("app.core.context", core_context)

services_callbacks = types.ModuleType("app.services.callbacks")
services_callbacks.send_callback = None
sys.modules.setdefault("app.services.callbacks", services_callbacks)

from app.agents.doc_ocr.callback import build_doc_ocr_callback_xml


class DocOCRCallbackTests(unittest.TestCase):
    def test_callback_xml_starts_without_newline_or_escaped_namespace_quotes(self) -> None:
        body = build_doc_ocr_callback_xml(
            request_header={
                "ChanlNo": "ch",
                "ReqSeqNo": "req-1",
                "ReqTime": "101112",
                "ReqDate": "20260526",
            },
            request_body={
                "BusinessSerlNo": "biz-1",
                "ResultFlag": 1,
                "FilePathAddr": "/tmp/result.pdf",
                "FailReason": "",
            },
        )

        self.assertTrue(body.startswith("<?xml version='1.0' encoding='utf8'?><soapenv:Envelope "))
        self.assertIn('xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"', body)
        self.assertNotIn("?>\n<soapenv:Envelope", body)
        self.assertNotIn(r"\"", body)


if __name__ == "__main__":
    unittest.main()
