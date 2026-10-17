"""Native clients receive complete snapshots, without request-specific filtering."""

import unittest
from unittest.mock import patch

from app.market import screening


class NativeSnapshotTests(unittest.TestCase):
    def test_complete_transport_for_each_market(self):
        for market, module in (("MY", "malaysia"), ("US", "us")):
            with (
                self.subTest(market=market),
                patch(
                    f"app.market.{module}.screener", return_value={"data": [], "total": 0}
                ) as provider,
            ):
                self.assertEqual(screening.snapshot(market), {"data": [], "total": 0})
                provider.assert_called_once_with(None, None, None, None, "symbol", False, 0, 20000)

    def test_unknown_market_is_rejected(self):
        with self.assertRaises(ValueError):
            screening.snapshot("UNKNOWN")
