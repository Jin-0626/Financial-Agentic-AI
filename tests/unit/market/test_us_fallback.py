"""Broad US fallback preserves exact values, stable pages and provider provenance."""

import unittest
from unittest.mock import patch

from app.market import nasdaq, raw_equities, transport, us


def download():
    return {
        "data": {
            "asOf": "Last price as of Oct 15, 2026",
            "rows": [
                {
                    "symbol": "B",
                    "name": "Company B",
                    "lastsale": "$9007199254740993.01",
                    "pctchange": "-2.5%",
                    "volume": "1,234",
                    "marketCap": "2,000",
                    "sector": "Technology",
                },
                {
                    "symbol": "A",
                    "name": "Company A",
                    "lastsale": "$10.25",
                    "pctchange": "3.5%",
                    "volume": "5,000",
                    "marketCap": "9,000",
                    "sector": "Industrials",
                },
                {"symbol": "BRK/A", "name": "Class-specific identity", "lastsale": "$10"},
                {"symbol": "C", "name": "Company C", "lastsale": "N/A", "volume": "N/A"},
            ],
        }
    }


class USFallbackTests(unittest.TestCase):
    def setUp(self):
        self.fallback = nasdaq.NasdaqSnapshot()

    def test_shared_download_stable_pages_and_exact_prices(self):
        with patch.object(transport, "fetch", return_value=download()) as fetch:
            first = self.fallback.page(0, 2)
            second = self.fallback.page(2, 2)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(first["total"], 3)
        self.assertEqual(first["_excluded"], 1)
        self.assertEqual(first["_snapshot_id"], second["_snapshot_id"])
        self.assertEqual([row["symbol"] for row in first["quotes"]], ["A", "B"])
        self.assertEqual(first["quotes"][1]["regularMarketPrice"], "9007199254740993.01")
        self.assertIsNone(second["quotes"][0]["regularMarketPrice"])
        self.assertNotIn("exchange", first["quotes"][0])
        self.assertNotIn("currency", first["quotes"][0])

    def test_empty_yahoo_uses_broad_feed_and_reports_source(self):
        with (
            patch.object(us, "fallback", self.fallback),
            patch.object(us, "universe", us.USSnapshot()),
            patch.object(us, "PAGE_SIZE", 2),
            patch.object(transport.yf, "screen", return_value={"total": 0, "quotes": []}) as yahoo,
            patch.object(transport, "fetch", return_value=download()),
        ):
            result = us.screener(None, None, None, None, "volume", True, 0, 8)
            raw = raw_equities.us_page(0)
        self.assertEqual(yahoo.call_count, 1)
        self.assertIn("Nasdaq", result["source"])
        self.assertEqual(result["total"], 3)
        self.assertEqual([row["instrument"]["symbol"] for row in result["data"]], ["A", "B", "C"])
        self.assertEqual(raw["data"]["feed"], "nasdaq")
        self.assertEqual(raw["data"]["excluded"], 1)
        self.assertIn("Oct 15", raw["data"]["as_of"])

    def test_yahoo_rate_limit_remains_authoritative(self):
        with (
            patch.object(us, "fallback", self.fallback),
            patch.object(transport.yf, "screen", side_effect=us.YFRateLimitError()),
            patch.object(transport, "fetch") as fetch,
        ):
            with self.assertRaises(transport.Throttled):
                us.fetch_page(0)
        fetch.assert_not_called()

    def test_empty_and_duplicate_fallbacks_fail_explicitly(self):
        duplicate = download()
        duplicate["data"]["rows"].append(duplicate["data"]["rows"][0])
        for response in ({"data": {"rows": []}}, duplicate):
            with (
                self.subTest(response=response),
                patch.object(transport, "fetch", return_value=response),
            ):
                with self.assertRaises(ValueError):
                    self.fallback.page(0, 250)

    def test_numeric_missing_values_are_not_zeroes(self):
        for value in (None, "N/A", "NaN", "Infinity", ""):
            self.assertIsNone(nasdaq.decimal_text(value))
        self.assertEqual(nasdaq.decimal_text("$1,234.5600"), "1234.5600")
