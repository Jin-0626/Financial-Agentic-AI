import concurrent.futures
import os
import unittest
from unittest.mock import patch

from app.market import malaysia, screening


def quote(symbol, price="2.00", volume=10, change=1, sector="Technology"):
    return {
        "instrument": {"id": "yahoo:" + symbol, "symbol": symbol, "name": "Fixture " + symbol},
        "currency": "MYR",
        "price": price,
        "volume": volume,
        "change_percent": change,
        "market_cap": 1000,
        "sector": sector,
        "observed_at": "2026-10-16T06:00:00+00:00",
    }


class MalaysiaTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"MY_EQUITY_SYMBOLS": "5326.KL,1155.KL,1295.KL"})
        self.environment.start()
        self.cache = patch.object(malaysia, "tracked", malaysia.MalaysiaSet())
        self.cache.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.cache.stop)

    def test_shared_snapshot_rankings_filters_and_pagination(self):
        rows = {
            "5326.KL": quote("5326.KL", "9007199254740993.01", 10, 3),
            "1155.KL": quote("1155.KL", "9007199254740993.02", 40, -2, "Financial Services"),
            "1295.KL": quote("1295.KL", "1.00", None, None),
        }
        with patch.object(malaysia, "fetch_quote", side_effect=lambda value: rows[value]) as fetch:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as worker:
                requests = [
                    worker.submit(
                        screening.screener, "MY", None, None, None, None, sort, True, 0, 8
                    )
                    for sort in ("change_percent", "volume")
                ]
                change, volume = [request.result() for request in requests]
            self.assertEqual(fetch.call_count, 3)
            self.assertEqual(change["data"][0]["instrument"]["symbol"], "5326.KL")
            self.assertEqual(volume["data"][0]["instrument"]["symbol"], "1155.KL")
            ascending = malaysia.screener(None, None, None, None, "volume", False, 0, 8)
            self.assertIsNone(ascending["data"][-1]["volume"])
            exact = malaysia.screener(None, None, None, None, "price", True, 0, 8)
            self.assertEqual(exact["data"][0]["instrument"]["symbol"], "1155.KL")
            selected = malaysia.screener("financial services", 500, -3, 20, "volume", True, 0, 1)
            self.assertEqual(selected["total"], 1)
            self.assertEqual(len(selected["data"]), 1)
            self.assertEqual(
                malaysia.screener(None, None, None, None, "volume", True, 3, 1)["data"], []
            )
            self.assertIn("not an exchange-wide", change["coverage"])
            self.assertEqual(change["observed_at"], "2026-10-16T06:00:00+00:00")

    def test_partial_failures_and_complete_failure_are_explicit(self):
        def load(value):
            if value != "5326.KL":
                raise ValueError("Fixture unavailable")
            return quote(value)

        with patch.object(malaysia, "fetch_quote", side_effect=load):
            result = malaysia.screener(None, None, None, None, "volume", True, 0, 8)
        self.assertIn("1 of 3", result["coverage"])
        self.assertIn("1155.KL", " ".join(result["limitations"]))
        with (
            patch.object(malaysia, "tracked", malaysia.MalaysiaSet()),
            patch.object(malaysia, "fetch_quote", side_effect=ValueError("down")),
        ):
            with self.assertRaisesRegex(ValueError, "unavailable"):
                malaysia.screener(None, None, None, None, "volume", True, 0, 8)

    def test_config_never_guesses_suffixes(self):
        for invalid in ("", "5326", "AAPL", "US.AAPL", "5326.KL,bad/symbol"):
            with (
                self.subTest(invalid=invalid),
                patch.dict(os.environ, {"MY_EQUITY_SYMBOLS": invalid}),
            ):
                with self.assertRaises(ValueError):
                    malaysia.symbols()
        with patch.dict(os.environ, {"MY_EQUITY_SYMBOLS": "yahoo:5326.KL;5326.KL"}):
            self.assertEqual(malaysia.symbols(), ("5326.KL",))

    def test_quote_identity_and_missing_values(self):
        info = {
            "symbol": "5326.KL",
            "shortName": "Fixture company",
            "currency": "MYR",
            "exchange": "KLS",
            "regularMarketPrice": 3.07,
        }
        with patch.object(malaysia.common, "ticker_info", return_value=(None, info)):
            row = malaysia.fetch_quote("5326.KL")
        self.assertEqual(row["instrument"]["name"], "Fixture company")
        self.assertIsNone(row["volume"])
        self.assertIsNone(row["observed_at"])
        with patch.object(
            malaysia.common, "ticker_info", return_value=(None, {**info, "currency": "USD"})
        ):
            with self.assertRaises(ValueError):
                malaysia.fetch_quote("5326.KL")

    def test_rate_limit_is_bounded_and_shared(self):
        with patch.object(
            malaysia, "fetch_quote", side_effect=malaysia.YFRateLimitError()
        ) as fetch:
            for _ in range(2):
                with self.assertRaises(malaysia.transport.Throttled):
                    malaysia.screener(None, None, None, None, "volume", True, 0, 8)
            self.assertLessEqual(fetch.call_count, 4)
