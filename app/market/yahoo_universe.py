"""Translate complete exchange partitions into stable logical Yahoo pages.

Yahoo clamps a region query to a 10,000-row window. Exchange partitions avoid
that window without filtering by price, capitalization or liquidity.
"""

import threading
import time
from app.market import common, transport


class CurrentEquityQuery(transport.yf.EquityQuery):
    @property
    def valid_values(self):
        values = super().valid_values
        # Yahoo returns these exchange codes; yfinance's catalogue omits them.
        return {
            **values,
            "exchange": {**values["exchange"], "us": values["exchange"]["us"] | {"OID", "TEX"}},
        }


class USDirectory:
    def __init__(self):
        self.lock = threading.Lock()
        self.cached = None

    def active(self):
        with self.lock:
            return self.cached is not None and time.monotonic() < self.cached[0]

    def page(self, offset, size, reported_total=None):
        with self.lock:
            if self.cached is None or time.monotonic() >= self.cached[0]:
                exchanges = sorted(
                    CurrentEquityQuery("eq", ["region", "us"]).valid_values["exchange"]["us"]
                )
                partitions = []
                for exchange in exchanges:
                    query = CurrentEquityQuery(
                        "and",
                        [
                            CurrentEquityQuery("eq", ["region", "us"]),
                            CurrentEquityQuery("eq", ["exchange", exchange]),
                        ],
                    )
                    response = transport.yf.screen(
                        query, offset=0, size=1, sortField="ticker", sortAsc=True
                    )
                    count = response.get("total")
                    if (
                        isinstance(count, bool)
                        or not isinstance(count, int)
                        or not 0 <= count <= 20000
                    ):
                        raise ValueError("Yahoo exchange partition exceeds its accessible window")
                    if count:
                        partitions.append((query, count))
                total = sum(count for _, count in partitions)
                if not 0 < total <= 50000:
                    raise ValueError("Yahoo exchange directory is empty or exceeds capacity")
                self.cached = (
                    time.monotonic() + 60,
                    partitions,
                    total,
                    common.now(),
                    reported_total,
                )
            _, partitions, total, revision, reported = self.cached
        rows = []
        start = 0
        for query, count in partitions:
            local = max(0, offset - start)
            while local < count and len(rows) < size and offset + len(rows) < total:
                available = min(count - local, size - len(rows), total - offset - len(rows))
                if local < 10000:
                    available = min(available, 10000 - local)
                ascending = local + available <= 10000
                upstream_offset = local if ascending else count - local - available
                response = transport.yf.screen(
                    query,
                    offset=upstream_offset,
                    size=available,
                    sortField="ticker",
                    sortAsc=ascending,
                )
                quotes = response.get("quotes")
                if (
                    response.get("total") != count
                    or response.get("start", upstream_offset) != upstream_offset
                    or not isinstance(quotes, list)
                    or len(quotes) != available
                ):
                    raise ValueError("Yahoo exchange pagination changed or was truncated")
                rows.extend(quotes if ascending else reversed(quotes))
                local += available
            start += count
            if len(rows) == size:
                break
        return {
            "total": total,
            "quotes": rows,
            "_feed": "yahoo",
            "_snapshot_id": revision,
            "_reported_total": reported,
        }


us_directory = USDirectory()
