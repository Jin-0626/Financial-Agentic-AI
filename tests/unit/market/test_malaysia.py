"""Complete Malaysian pagination and global ranking without tracked-set restrictions."""

import os
import unittest
from unittest.mock import patch
from app.market import malaysia, us, transport


def quote(index):
    return {
        "symbol": f"{index:04}.KL",
        "shortName": f"Company {index}",
        "currency": "MYR",
        "exchange": "KLS",
        "regularMarketPrice": str(index + 1),
        "regularMarketVolume": index,
        "regularMarketChangePercent": index - 2,
        "sector": "Technology",
        "marketCap": 1000,
    }


class MalaysiaTests(unittest.TestCase):
    def setUp(self):
        self.cache = patch.object(malaysia, "universe", us.EquitySnapshot("MY"))
        self.cache.start()
        self.addCleanup(self.cache.stop)

    def test_complete_universe_is_ranked_before_paging_and_ignores_legacy_set(self):
        def page(offset, region):
            self.assertEqual(region, "my")
            return {"total": 5, "quotes": [quote(i) for i in range(offset, min(5, offset + 2))]}

        with (
            patch.dict(os.environ, {"MY_EQUITY_SYMBOLS": "5326.KL"}),
            patch.object(us, "PAGE_SIZE", 2),
            patch.object(us, "fetch_page", side_effect=page) as fetch,
        ):
            result = malaysia.screener(None, None, None, None, "volume", True, 0, 2)
            self.assertEqual(result["total"], 5)
            self.assertEqual(
                [row["instrument"]["symbol"] for row in result["data"]], ["0004.KL", "0003.KL"]
            )
            self.assertEqual(fetch.call_count, 3)
            last = malaysia.screener(None, None, None, None, "symbol", False, 4, 2)
            self.assertEqual(last["data"][0]["instrument"]["symbol"], "0004.KL")
            self.assertEqual(fetch.call_count, 3)
            self.assertIn("MY", result["source"])
            self.assertNotIn("tracked", result["coverage"])
            filtered = malaysia.screener("Technology", None, None, 4, "volume", True, 0, 100)
            self.assertEqual(filtered["total"], 1)

    def test_incomplete_pages_are_not_presented_as_a_complete_universe(self):
        with patch.object(us, "fetch_page", return_value={"total": 5, "quotes": [quote(0)]}):
            with self.assertRaises(ValueError):
                malaysia.screener(None, None, None, None, "volume", True, 0, 100)

    def test_rate_limit_is_shared_without_repeated_upstream_requests(self):
        with patch.object(us, "fetch_page", side_effect=transport.Throttled(60)) as fetch:
            for _ in range(2):
                with self.assertRaises(transport.Throttled):
                    malaysia.screener(None, None, None, None, "volume", True, 0, 100)
            self.assertEqual(fetch.call_count, 1)

    def test_wrong_region_identity_is_rejected(self):
        with patch.object(
            us,
            "fetch_page",
            return_value={
                "total": 1,
                "quotes": [{**quote(0), "symbol": "AAPL", "currency": "USD"}],
            },
        ):
            with self.assertRaises(ValueError):
                malaysia.screener(None, None, None, None, "symbol", False, 0, 100)

    def test_legacy_config_never_guesses_suffixes(self):
        for invalid in ("", "5326", "AAPL", "US.AAPL", "5326.KL,bad/symbol"):
            with (
                self.subTest(invalid=invalid),
                patch.dict(os.environ, {"MY_EQUITY_SYMBOLS": invalid}),
            ):
                with self.assertRaises(ValueError):
                    malaysia.symbols()
