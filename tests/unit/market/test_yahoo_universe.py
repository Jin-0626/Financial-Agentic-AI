"""Yahoo exchange partitions must avoid the provider's 10,000-row offset clamp."""

import unittest
from unittest.mock import patch
from app.market import transport
from app.market.yahoo_universe import USDirectory


class DirectoryTests(unittest.TestCase):
    def test_tail_pages_span_exchanges_and_reverse_the_accessible_tail_window(self):
        directory = USDirectory()
        requests = []

        def screen(query, **kwargs):
            exchange = query.to_dict()["operands"][1]["operands"][1]
            total = {"NMS": 10001, "NYQ": 3}.get(exchange, 0)
            offset, size = kwargs["offset"], kwargs["size"]
            requests.append((exchange, offset, size, kwargs["sortAsc"]))
            indexes = range(offset, min(offset + size, total))
            if not kwargs["sortAsc"]:
                indexes = [total - 1 - i for i in indexes]
            return {
                "start": offset,
                "total": total,
                "quotes": [{"symbol": f"{exchange}{i}"} for i in indexes],
            }

        with patch.object(transport.yf, "screen", side_effect=screen):
            result = directory.page(10000, 250, 10004)
        self.assertEqual(result["total"], 10004)
        self.assertEqual(
            [row["symbol"] for row in result["quotes"]], ["NMS10000", "NYQ0", "NYQ1", "NYQ2"]
        )
        self.assertIn(("NMS", 0, 1, False), requests)
        self.assertTrue(all(offset + size <= 10000 for _, offset, size, _ in requests))

    def test_page_crossing_two_accessible_windows_has_no_gap(self):
        def screen(query, **kwargs):
            exchange = query.to_dict()["operands"][1]["operands"][1]
            total = 20000 if exchange == "NMS" else 0
            offset, size = kwargs["offset"], kwargs["size"]
            self.assertLessEqual(offset + size, 10000)
            indexes = range(offset, min(offset + size, total))
            if not kwargs["sortAsc"]:
                indexes = [total - 1 - i for i in indexes]
            return {
                "start": offset,
                "total": total,
                "quotes": [{"symbol": f"S{i:05}"} for i in indexes],
            }

        with patch.object(transport.yf, "screen", side_effect=screen):
            result = USDirectory().page(9900, 250, 20000)
        self.assertEqual(
            [row["symbol"] for row in result["quotes"]], [f"S{i:05}" for i in range(9900, 10150)]
        )

    def test_oversized_partitions_fail_instead_of_publishing_incomplete_data(self):
        with patch.object(transport.yf, "screen", return_value={"total": 20001, "quotes": []}):
            with self.assertRaises(ValueError):
                USDirectory().page(0, 250)
