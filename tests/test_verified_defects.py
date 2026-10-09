import importlib
import json
import math
import os
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from app import routes, financial_tools as finance

ROOT = Path(__file__).resolve().parents[1]


class VerifiedDefectTests(unittest.TestCase):

    def test_removed_file_routes_return_410_without_agent_or_backend_access(self):
        from fastapi import FastAPI
        api = FastAPI()
        api.include_router(routes.router, prefix="/api")
        with patch.object(routes, "_agent", None):
            with TestClient(api) as client:
                for path in ("/api/files/upload", "/api/chat-with-file", "/api/chat-with-file/stream"):
                    self.assertEqual(client.post(path, json={"file_id":"retired"}).status_code,410)


    def treasury(self, quotes=None, exception=None, fmp=None):
        provider = Mock()
        if exception:
            provider.get_batch_quotes.side_effect = exception
        else:
            provider.get_batch_quotes.return_value = quotes
        with patch.object(finance, "stock_data", provider), patch.object(finance, "_fmp_client", fmp):
            return json.loads(finance._make_economics_tool().invoke({"indicator": "treasury"}))

    def quotes(self):
        return [{"symbol": symbol, "price": 4.0} for symbol in ("^IRX", "^FVX", "^TNX", "^TYX")]

    def test_treasury_valid_results(self):
        result = self.treasury(self.quotes())
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["treasury_yield_proxies"], self.quotes())
        self.assertEqual(result["provider_errors"], [])
        self.assertIsNone(result["error"])

    def test_treasury_empty_response(self):
        result = self.treasury([])
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["treasury_yield_proxies"], [])
        self.assertTrue(result["provider_errors"])

    def test_treasury_provider_exception_is_sanitized(self):
        with patch.dict(os.environ, {"FMP_API_KEY": "fixture-private-key"}):
            result = self.treasury(exception=RuntimeError("HTTP 401 fixture-private-key"))
        self.assertEqual(result["status"], "error")
        self.assertNotIn("fixture-private-key", json.dumps(result))
        self.assertIn("401", json.dumps(result))

    def test_treasury_error_containing_response(self):
        result = self.treasury([{"error": "HTTP 429"}])
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["treasury_yield_proxies"], [])
        self.assertIn("429", json.dumps(result["provider_errors"]))

    def test_treasury_partial_success_preserves_valid_quotes(self):
        valid = self.quotes()[:2]
        result = self.treasury(valid + [{"symbol": "^TNX", "error": "HTTP 429"}, {"symbol": "^TYX", "price": None}])
        self.assertEqual(result["status"], "partial_success")
        self.assertEqual(result["treasury_yield_proxies"], valid)
        self.assertTrue(result["provider_errors"])

    def test_treasury_fmp_failure_kept_when_yahoo_succeeds(self):
        fmp = Mock(api_key="fixture")
        fmp.get_treasury_rates.side_effect = RuntimeError("HTTP 403")
        result = self.treasury(self.quotes(), fmp=fmp)
        self.assertEqual(result["status"], "partial_success")
        self.assertEqual(result["provider_errors"][0]["provider"], "FMP")
        self.assertEqual(len(result["treasury_yield_proxies"]), 4)

    def test_treasury_fmp_empty_and_yahoo_error_is_complete_failure(self):
        fmp = Mock(api_key="fixture")
        fmp.get_treasury_rates.return_value = []
        result = self.treasury([{"error": "HTTP 429"}], fmp=fmp)
        self.assertEqual(result["status"], "error")
        self.assertEqual({e["provider"] for e in result["provider_errors"]}, {"FMP", "Yahoo"})

    def test_treasury_fmp_partial_rows_retained_with_valid_proxies(self):
        fmp = Mock(api_key="fixture")
        fmp.get_treasury_rates.return_value = [{"date": "2026-10-05", "year10": 4.1}, {"error": "provider failed"}]
        result = self.treasury(self.quotes(), fmp=fmp)
        self.assertEqual(result["status"], "partial_success")
        self.assertEqual(result["treasury_rates"], [{"date": "2026-10-05", "year10": 4.1}])
        self.assertEqual(len(result["treasury_yield_proxies"]), 4)

    def test_treasury_fmp_valid_has_same_schema(self):
        fmp = Mock(api_key="fixture")
        rows = [{"date": "2026-10-05", "year10": 4.1}]
        fmp.get_treasury_rates.return_value = rows
        result = self.treasury([], fmp=fmp)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["treasury_rates"], rows)
        self.assertEqual(result["treasury_yield_proxies"], [])
        self.assertEqual(set(result), set(self.treasury(self.quotes())))

    def test_invalid_numeric_quote_is_not_success(self):
        for price in (None, True, "4", math.nan, math.inf):
            with self.subTest(price=price):
                self.assertEqual(self.treasury([{"symbol": "^IRX", "price": price}])["status"], "error")






if __name__ == "__main__":
    unittest.main()
