import concurrent.futures
import threading
import time
import unittest
from unittest.mock import patch

from app.market import screening, us


def quote(symbol, volume=None, change=None, price=1, sector="Technology"):
    return {
        "symbol": symbol,
        "shortName": "Fixture " + symbol,
        "currency": "USD",
        "exchange": "NMS",
        "regularMarketPrice": price,
        "regularMarketVolume": volume,
        "regularMarketChangePercent": change,
        "marketCap": 1000,
        "sector": sector,
        "regularMarketTime": 1792094400,
    }


class USSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.cache = patch.object(us, "universe", us.USSnapshot())
        self.cache.start()
        self.pages = patch.object(us, "PAGE_SIZE", 2)
        self.pages.start()
        self.addCleanup(self.cache.stop)
        self.addCleanup(self.pages.stop)
        self.rows = [
            quote("A", 2, -4),
            quote("B", 300, 1),
            quote("C", 5, 20),
            quote("D"),
            quote("E", 400, 3),
            quote("F", 1, -10, sector="Utilities"),
        ]

    def page(self, offset):
        return {"total": len(self.rows), "quotes": self.rows[offset : offset + us.PAGE_SIZE]}

    def test_broad_snapshot_ranks_across_pages_and_shares_refresh(self):
        with patch.object(us, "fetch_page", side_effect=self.page) as fetch:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as worker:
                tasks = [
                    worker.submit(
                        screening.screener, "US", None, None, None, None, sort, True, 0, 2
                    )
                    for sort in ("volume", "change_percent")
                ]
                volume, change = [task.result() for task in tasks]
            self.assertEqual(fetch.call_count, 3)
            self.assertEqual([row["instrument"]["symbol"] for row in volume["data"]], ["E", "B"])
            self.assertEqual([row["instrument"]["symbol"] for row in change["data"]], ["C", "E"])
            self.assertEqual(volume["total"], 6)
            self.assertIn("not verified", volume["coverage"])
            ascending = us.screener(None, None, None, None, "volume", False, 0, 8)
            self.assertIsNone(ascending["data"][-1]["volume"])
            selected = us.screener("technology", 500, 0, 5, "volume", True, 1, 2)
            self.assertEqual(selected["total"], 3)
            self.assertEqual([row["instrument"]["symbol"] for row in selected["data"]], ["B", "C"])

    def test_stable_symbol_pagination_and_exact_prices(self):
        with patch.object(us, "fetch_page", side_effect=self.page):
            result = us.screener(None, None, None, None, "symbol", False, 2, 2)
            self.assertEqual([row["instrument"]["symbol"] for row in result["data"]], ["C", "D"])
        rows = [
            {**us.normalize(quote("A")), "price": "9007199254740993.01"},
            {**us.normalize(quote("B")), "price": "9007199254740993.02"},
        ]
        with patch.object(us.universe, "snapshot", return_value=(rows, "2026-10-16T00:00:00Z")):
            result = us.screener(None, None, None, None, "price", True, 0, 2)
            self.assertEqual(result["data"][0]["instrument"]["symbol"], "B")

    def test_missing_duplicate_changed_and_oversized_pages_fail(self):
        for bad in (None, [], [quote("A"), quote("A")]):
            with (
                self.subTest(bad=bad),
                patch.object(us, "universe", us.USSnapshot()),
                patch.object(us, "fetch_page", return_value={"total": 2, "quotes": bad}),
            ):
                with self.assertRaises(ValueError):
                    us.screener(None, None, None, None, "volume", True, 0, 8)
        for total in (None, True, us.MAX_ROWS + 1):
            with (
                self.subTest(total=total),
                patch.object(us, "universe", us.USSnapshot()),
                patch.object(us, "fetch_page", return_value={"total": total, "quotes": []}),
            ):
                with self.assertRaises(ValueError):
                    us.universe.snapshot()

        def changed(offset):
            return {**self.page(offset), "total": 6 if offset == 0 else 7}

        with (
            patch.object(us, "universe", us.USSnapshot()),
            patch.object(us, "fetch_page", side_effect=changed),
        ):
            with self.assertRaises(ValueError):
                us.universe.snapshot()

    def test_rate_limits_back_off_without_replacing_success(self):
        with patch.object(us, "fetch_page", side_effect=self.page):
            first = us.universe.snapshot()
        us.universe.cached = (0, *us.universe.cached[1:])
        with patch.object(us, "fetch_page", side_effect=us.transport.Throttled(60)) as fetch:
            for _ in range(2):
                with self.assertRaises(us.transport.Throttled):
                    us.universe.snapshot()
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(us.universe.cached[1:], first)

    def test_page_workers_are_bounded(self):
        active = maximum = 0
        lock = threading.Lock()
        self.rows = [quote(f"STOCK{index:02}") for index in range(20)]

        def read(offset):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            try:
                time.sleep(0.005)
                return self.page(offset)
            finally:
                with lock:
                    active -= 1

        with patch.object(us, "fetch_page", side_effect=read):
            us.universe.snapshot()
        self.assertLessEqual(maximum, 4)

    def test_empty_universe_is_distinct_from_failure(self):
        with patch.object(us, "fetch_page", return_value={"total": 0, "quotes": []}):
            result = us.screener(None, None, None, None, "volume", True, 0, 8)
        self.assertEqual(result["data"], [])
        self.assertEqual(result["total"], 0)
