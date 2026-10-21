"""Transport contracts for Rust-owned equity assembly and normalization."""

import unittest
from unittest.mock import patch

from yfinance.exceptions import YFRateLimitError

from app.market import raw_equities, transport


class RawEquityTests(unittest.TestCase):
    def test_raw_price_precision_names_and_missing_fields(self):
        row = raw_equities.project(
            {
                "symbol": "A",
                "regularMarketPrice": 9007199254740993,
                "shortname": "Example",
                "regularMarketTime": 1700000000,
                "regularMarketVolume": None,
                "marketCap": float("nan"),
            }
        )
        self.assertEqual(row["regularMarketPrice"], "9007199254740993")
        self.assertEqual(row["shortName"], "Example")
        self.assertNotIn("shortname", row)  # Avoid duplicate serde alias fields.
        self.assertIsNone(row["regularMarketVolume"])
        self.assertIsNone(row["marketCap"])
        self.assertEqual(row["regularMarketTime"], 1700000000)

    def test_invalid_numbers_remain_unavailable(self):
        for value in (True, float("nan"), float("inf"), None):
            with self.subTest(value=value):
                self.assertIsNone(
                    raw_equities.project({"regularMarketPrice": value})["regularMarketPrice"]
                )

    def test_us_page_preserves_count_and_broad_provider_membership(self):
        page = {
            "total": 1000,
            "quotes": [{"symbol": "OTC", "exchange": "PNK", "regularMarketPrice": 0.00001}],
        }
        with patch("app.market.us.fetch_page", return_value=page) as fetch:
            result = raw_equities.us_page(250)
        fetch.assert_called_once_with(250)
        self.assertEqual(result["data"]["total"], 1000)
        self.assertEqual(result["data"]["quotes"][0]["exchange"], "PNK")

    def test_configured_malaysia_set_is_transport_data(self):
        with patch("app.market.malaysia.symbols", return_value=("5326.KL", "1155.KL")):
            self.assertEqual(raw_equities.my_symbols()["data"], ["5326.KL", "1155.KL"])

    def test_exact_identity_and_rate_delay(self):
        for identity in ("coingecko:bitcoin", "yahoo:AAPL", "5326.KL"):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                raw_equities.equity_info(identity)
        with patch("app.market.common.ticker_info", side_effect=YFRateLimitError()):
            with self.assertRaises(transport.Throttled) as error:
                raw_equities.equity_info("yahoo:5326.KL")
            self.assertEqual(error.exception.seconds, 60)

    def test_transport_does_not_run_python_snapshot_processing(self):
        with (
            patch(
                "app.market.us.universe.snapshot",
                side_effect=AssertionError("Python snapshot invoked"),
            ),
            patch("app.market.us.fetch_page", return_value={"total": 0, "quotes": []}),
        ):
            data = raw_equities.us_page(0)["data"]
            self.assertEqual(data["total"], 0)
            self.assertEqual(data["quotes"], [])
            self.assertEqual(data["feed"], "yahoo")
