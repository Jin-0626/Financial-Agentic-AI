"""Financial Deep Agents naming and provider-integrity regressions."""

import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

from app.context_type import MemoryContext
from app.models import ChatRequest
from app.research_output import request_mode

ROOT = Path(__file__).resolve().parents[1]


class FinancialBrandingTests(unittest.TestCase):
    def test_financial_selector_is_consistent_across_request_and_runtime(self):
        self.assertEqual(ChatRequest().response_schema, "financial")
        self.assertEqual(MemoryContext().response_schema, "financial")
        self.assertEqual(request_mode({}), "financial")
        self.assertEqual(request_mode({"response_schema": "financial"}), "financial")
        self.assertEqual(request_mode({"response_schema": "analysis_report"}), "financial")
        self.assertIsNone(request_mode({"response_schema": None}))
        self.assertEqual(ChatRequest(response_schema="financial").model_dump()["response_schema"], "financial")

    def test_all_provider_files_match_execution_manifest(self):
        directory = ROOT / "scripts/data_sources"
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("financial_worker.py", manifest["runner"])
        self.assertTrue(manifest["scripts"])
        for record in manifest["scripts"]:
            with self.subTest(provider=record["file"]):
                path = directory / record["file"]
                self.assertTrue(path.is_file())
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record["sha256"])

    def test_renamed_json_module_preserves_nonfinite_sanitization(self):
        path = ROOT / "scripts/data_sources/financial_json.py"
        spec = importlib.util.spec_from_file_location("financial_json", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        raw = {"price": 12.5, "missing": float("nan"), "infinite": float("inf")}
        expected = {"price": 12.5, "missing": None, "infinite": None}
        self.assertEqual(json.loads(module.dumps(raw)), expected)
        self.assertEqual(json.loads(module.dumps_bytes(raw)), expected)
