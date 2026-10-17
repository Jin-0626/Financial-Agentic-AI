"""Broad US provider snapshot ranked by the quote values actually displayed."""

import copy
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal

from yfinance.exceptions import YFRateLimitError

from app.market import common, transport
from app.market.listing import page_rows

PAGE_SIZE = 250
MAX_ROWS = 20_000


@transport.yahoo_bound
def fetch_page(offset):
    query = transport.yf.EquityQuery("eq", ["region", "us"])
    try:
        return transport.yf.screen(
            query, offset=offset, size=PAGE_SIZE, sortField="ticker", sortAsc=True
        )
    except YFRateLimitError as error:
        raise transport.Throttled(60) from error


def normalize(item):
    value = item.get("symbol")
    if not isinstance(value, str) or not value:
        raise ValueError("US provider returned a missing security identity")
    timestamp = common.number(item.get("regularMarketTime"))
    observed = (
        datetime.fromtimestamp(timestamp, UTC).isoformat() if timestamp and timestamp > 0 else None
    )
    price = common.decimal(item.get("regularMarketPrice"))
    return {
        "instrument": common.instrument(value, item),
        "currency": item.get("currency"),
        "sector": item.get("sector") or "Unclassified",
        "market_cap": common.number(item.get("marketCap")),
        "price": price if price is not None and Decimal(price) >= 0 else None,
        "volume": common.number(item.get("regularMarketVolume")),
        "change_percent": common.number(item.get("regularMarketChangePercent")),
        "observed_at": observed,
    }


class USSnapshot:
    def __init__(self):
        self.lock = threading.Lock()
        self.cached = None
        self.failure = None

    def snapshot(self):
        with self.lock:
            if self.failure and time.monotonic() < self.failure[0]:
                if self.failure[1]:
                    raise transport.Throttled(math.ceil(self.failure[0] - time.monotonic()))
                raise ValueError("US provider snapshot unavailable")
            if self.cached and time.monotonic() < self.cached[0]:
                return self.cached[1:]
            errors = []
            error_lock = threading.Lock()
            try:
                first = fetch_page(0)
                total = first.get("total")
                if (
                    isinstance(total, bool)
                    or not isinstance(total, int)
                    or not 0 <= total <= MAX_ROWS
                ):
                    raise ValueError(
                        "US provider universe exceeds the supported snapshot size or has no valid count"
                    )
                stopped = threading.Event()

                def read(offset):
                    if stopped.is_set():
                        with error_lock:
                            error = (
                                errors[0]
                                if errors
                                else ValueError("US provider snapshot interrupted")
                            )
                        raise error
                    try:
                        return fetch_page(offset)
                    except Exception as error:
                        with error_lock:
                            errors.append(error)
                        stopped.set()
                        raise

                rows = []
                seen = set()

                def append(page, offset):
                    values = page.get("quotes") if isinstance(page, dict) else None
                    expected = min(PAGE_SIZE, total - offset)
                    if (
                        not isinstance(values, list)
                        or len(values) != expected
                        or page.get("total") != total
                    ):
                        raise ValueError(
                            "US provider pagination changed or returned an incomplete page"
                        )
                    for value in values:
                        row = normalize(value)
                        identity = row["instrument"]["id"]
                        if identity in seen:
                            raise ValueError(
                                "US provider repeated a security across snapshot pages"
                            )
                        seen.add(identity)
                        rows.append(row)

                append(first, 0)
                offsets = range(PAGE_SIZE, total, PAGE_SIZE)
                with ThreadPoolExecutor(max_workers=4, thread_name_prefix="us-screener") as workers:
                    for offset, page in zip(offsets, workers.map(read, offsets)):
                        try:
                            append(page, offset)
                        except Exception:
                            stopped.set()
                            raise
                fetched_at = common.now()
                self.cached = (time.monotonic() + 60, rows, fetched_at)
                self.failure = None
                return self.cached[1:]
            except Exception as error:
                limited = isinstance(error, transport.Throttled) or any(
                    isinstance(value, transport.Throttled) for value in errors
                )
                self.failure = (time.monotonic() + (60 if limited else 30), limited)
                if limited and not isinstance(error, transport.Throttled):
                    raise transport.Throttled(60) from error
                raise


universe = USSnapshot()


def screener(sector, min_cap, min_change, min_volume, sort, descending, offset, limit):
    rows, fetched_at = universe.snapshot()
    page, total = page_rows(
        rows, sector, min_cap, min_change, min_volume, sort, descending, offset, limit
    )
    observations = [row["observed_at"] for row in page if row["observed_at"] is not None]
    result = common.result(
        copy.deepcopy(page),
        "Yahoo Finance screener · US quote snapshot",
        f"{len(rows)} provider-listed US equities; not verified as every listed issuer. Ranked by displayed regular-session quote values.",
        total=total,
        offset=offset,
        limit=limit,
        market="US",
        observed_at=min(observations) if page and len(observations) == len(page) else None,
        limitations=[
            "Broad provider coverage includes OTC securities and provider-classified equity instruments. No curated stock basket or minimum liquidity filter is imposed.",
            "Quotes may be delayed and observations are not simultaneous. Rankings use regular-session values, not pre/post-market changes. Observation time is the oldest quote on this page.",
        ],
    )
    result["fetched_at"] = fetched_at
    return result
