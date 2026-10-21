"""Single-flight broad US fallback using the project's existing Nasdaq provider."""

import re
import threading
import time
from decimal import Decimal, InvalidOperation
import requests

from app.market import common, transport


def decimal_text(value):
    if not isinstance(value, str):
        return None
    value = value.strip().removeprefix("$").replace(",", "").removesuffix("%")
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return str(number) if number.is_finite() else None


def number(value):
    value = decimal_text(value)
    return common.number(float(value)) if value is not None else None


class NasdaqSnapshot:
    def __init__(self):
        self.lock = threading.Lock()
        self.cached = None

    def active(self):
        with self.lock:
            return self.cached is not None and time.monotonic() < self.cached[0]

    def page(self, offset, size):
        with self.lock:
            if self.cached is None or time.monotonic() >= self.cached[0]:
                raw = transport.fetch(
                    "https://api.nasdaq.com/api/screener/stocks",
                    {"download": "true"},
                    {"User-Agent": "Financial-Deep-Agents/1.0", "Accept": "application/json"},
                )
                data = raw.get("data") or {}
                source_rows = data.get("rows")
                if not isinstance(source_rows, list) or not 0 < len(source_rows) <= 50000:
                    raise ValueError("Nasdaq fallback returned an empty or oversized universe")
                rows = []
                seen = set()
                for source in source_rows:
                    symbol = source.get("symbol", "")
                    # Preferred/class-share syntax is provider-specific; never guess Yahoo mappings.
                    if not isinstance(symbol, str) or not re.fullmatch(
                        r"[A-Z0-9][A-Z0-9-]{0,31}", symbol
                    ):
                        continue
                    if symbol in seen:
                        raise ValueError("Nasdaq fallback repeated a security identity")
                    seen.add(symbol)
                    rows.append(
                        {
                            "symbol": symbol,
                            "shortName": source.get("name"),
                            "quoteType": "EQUITY",
                            "regularMarketPrice": decimal_text(source.get("lastsale")),
                            "regularMarketChangePercent": number(source.get("pctchange")),
                            "regularMarketVolume": number(source.get("volume")),
                            "marketCap": number(source.get("marketCap")),
                            "sector": source.get("sector"),
                        }
                    )
                if not rows:
                    raise ValueError("Nasdaq fallback returned no supported security identities")
                rows.sort(key=lambda row: row["symbol"])
                as_of = data.get("asOf")
                if not isinstance(as_of, str):
                    try:
                        metadata = transport.fetch(
                            "https://api.nasdaq.com/api/screener/stocks",
                            {"tableonly": "true", "limit": 1, "offset": 0},
                            {
                                "User-Agent": "Financial-Deep-Agents/1.0",
                                "Accept": "application/json",
                            },
                        )
                        as_of = (metadata.get("data") or {}).get("asof")
                    except (requests.RequestException, ValueError, transport.Throttled):
                        as_of = None
                if isinstance(as_of, str):
                    as_of = as_of.strip() or None
                else:
                    as_of = None
                self.cached = (
                    time.monotonic() + 60,
                    rows,
                    as_of,
                    len(source_rows) - len(rows),
                    common.now(),
                )
            _, rows, as_of, excluded, revision = self.cached
            return {
                "total": len(rows),
                "quotes": rows[offset : offset + size],
                "_feed": "nasdaq",
                "_as_of": as_of,
                "_excluded": excluded,
                "_snapshot_id": revision,
            }


fallback = NasdaqSnapshot()
