"""Live Yahoo quotes for an explicit Malaysian tracked set, not an exchange directory."""

import os
import copy
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal

from yfinance.exceptions import YFRateLimitError

from app.market import common, transport
from app.market.listing import page_rows

DEFAULT_SYMBOLS = (
    "5326.KL",
    "1155.KL",
    "1295.KL",
    "1023.KL",
    "5347.KL",
    "5225.KL",
    "5819.KL",
    "4863.KL",
    "6947.KL",
    "6888.KL",
    "6033.KL",
    "5183.KL",
    "5285.KL",
    "5398.KL",
    "5296.KL",
    "3816.KL",
)


def symbols():
    configured = os.getenv("MY_EQUITY_SYMBOLS")
    values = DEFAULT_SYMBOLS if configured is None else re.split(r"[,;\s]+", configured.strip())
    result = tuple(dict.fromkeys(common.symbol(value.removeprefix("yahoo:")) for value in values))
    if not 1 <= len(result) <= 128 or any(not value.endswith(".KL") for value in result):
        raise ValueError("MY_EQUITY_SYMBOLS requires 1–128 exact Yahoo .KL identifiers")
    return result


@transport.yahoo_bound
def fetch_quote(value):
    _, info = common.ticker_info(value)
    if info.get("currency") != "MYR" or info.get("exchange") not in (None, "KLSE"):
        raise ValueError("Malaysian quote identity/currency mismatch")
    price = common.decimal(info.get("regularMarketPrice"))
    if price is None or Decimal(price) <= 0:
        raise ValueError("Malaysian quote price unavailable")
    timestamp = common.number(info.get("regularMarketTime"))
    observed = (
        datetime.fromtimestamp(timestamp, UTC).isoformat() if timestamp and timestamp > 0 else None
    )
    return {
        "instrument": common.instrument(value, info),
        "currency": "MYR",
        "sector": info.get("sector") or "Unclassified",
        "market_cap": common.number(info.get("marketCap")),
        "price": price,
        "volume": common.number(info.get("regularMarketVolume")),
        "change_percent": common.number(info.get("regularMarketChangePercent")),
        "observed_at": observed,
    }


class MalaysiaSet:
    def __init__(self):
        self.lock = threading.Lock()
        self.cached = None
        self.failure = None

    def snapshot(self, requested):
        # Both overview sorts share one bounded refresh, rather than refetching each symbol.
        with self.lock:
            if self.failure and self.failure[0] == requested and time.monotonic() < self.failure[1]:
                if self.failure[2]:
                    raise transport.Throttled(math.ceil(self.failure[1] - time.monotonic()))
                raise ValueError("Malaysian tracked-set quotes unavailable")
            if self.cached and self.cached[0] == requested and time.monotonic() < self.cached[1]:
                return self.cached[2:]
            stopped = threading.Event()

            def read(value):
                if stopped.is_set():
                    return None
                try:
                    return fetch_quote(value)
                except YFRateLimitError:
                    stopped.set()
                    return None
                except Exception:
                    return None

            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="my-quotes") as workers:
                fetched = list(workers.map(read, requested))
            rows = [row for row in fetched if row is not None]
            if not rows:
                self.failure = (
                    requested,
                    time.monotonic() + (60 if stopped.is_set() else 30),
                    stopped.is_set(),
                )
                if stopped.is_set():
                    raise transport.Throttled(60)
                raise ValueError("Malaysian tracked-set quotes unavailable")
            missing = [value for value, row in zip(requested, fetched) if row is None]
            self.failure = None
            fetched_at = common.now()
            self.cached = (
                requested,
                time.monotonic() + 60,
                rows,
                missing,
                fetched_at,
                stopped.is_set(),
            )
            return self.cached[2:]


tracked = MalaysiaSet()


def screener(sector, min_cap, min_change, min_volume, sort, descending, offset, limit):
    requested = symbols()
    rows, missing, fetched_at, rate_limited = tracked.snapshot(requested)
    page, total = page_rows(
        rows, sector, min_cap, min_change, min_volume, sort, descending, offset, limit
    )
    observations = [row["observed_at"] for row in page if row["observed_at"] is not None]
    limitations = [
        "Rankings and filters apply only to the configured tracked set, not all Bursa listings. Quotes may be delayed; observations are not simultaneous."
    ]
    if missing:
        limitations.append("Unavailable tracked quotes omitted: " + ", ".join(missing))
    if rate_limited:
        limitations.append(
            "Refresh stopped after provider rate limiting; returned coverage is partial."
        )
    result = common.result(
        copy.deepcopy(page),
        "Yahoo Finance quotes · MY tracked set",
        f"{len(rows)} of {len(requested)} tracked Malaysian equities available; not an exchange-wide listing",
        total=total,
        offset=offset,
        limit=limit,
        market="MY",
        limitations=limitations,
        observed_at=min(observations) if len(observations) == len(page) and page else None,
    )
    result["fetched_at"] = fetched_at
    if rate_limited:
        result["retry_after"] = 60
    return result
