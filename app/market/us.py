"""Broad Equity provider snapshot ranked by the quote values actually displayed."""

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
from app.market.nasdaq import fallback
from app.market.yahoo_universe import us_directory

PAGE_SIZE = 250
MAX_ROWS = 50_000


@transport.yahoo_bound
def fetch_page(offset, region="us"):
    if region not in {"us", "my"}:
        raise ValueError("Unsupported equity region")
    if region == "us" and fallback.active():
        return fallback.page(offset, PAGE_SIZE)
    query = transport.yf.EquityQuery("eq", ["region", region])
    try:
        if region == "us" and us_directory.active():
            return us_directory.page(offset, PAGE_SIZE)
        page = transport.yf.screen(
            query, offset=offset, size=PAGE_SIZE, sortField="ticker", sortAsc=True
        )
        if region == "us" and isinstance(page.get("total"), int) and page["total"] > 10000:
            return us_directory.page(offset, PAGE_SIZE, page["total"])
        if region == "us" and page.get("total") == 0 and page.get("quotes") == []:
            return fallback.page(offset, PAGE_SIZE)
        return page
    except YFRateLimitError as error:
        raise transport.Throttled(60) from error


def normalize(item):
    value = item.get("symbol")
    if not isinstance(value, str) or not value:
        raise ValueError("Equity provider returned a missing security identity")
    timestamp = common.number(item.get("regularMarketTime"))
    observed = (
        datetime.fromtimestamp(timestamp, UTC).isoformat() if timestamp and timestamp > 0 else None
    )
    value_price = item.get("regularMarketPrice")
    price = value_price if isinstance(value_price, str) else common.decimal(value_price)
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


class EquitySnapshot:
    def __init__(self, market="US"):
        if market not in {"US", "MY"}:
            raise ValueError("Unsupported equity market")
        self.market = market
        self.lock = threading.Lock()
        self.cached = None
        self.failure = None
        self.provenance = {}

    def snapshot(self):
        with self.lock:
            if self.failure and time.monotonic() < self.failure[0]:
                if self.failure[1]:
                    raise transport.Throttled(math.ceil(self.failure[0] - time.monotonic()))
                raise ValueError("Equity provider snapshot unavailable")
            if self.cached and time.monotonic() < self.cached[0]:
                return self.cached[1:]
            errors = []
            error_lock = threading.Lock()
            try:

                def fetch(offset):
                    return fetch_page(offset) if self.market == "US" else fetch_page(offset, "my")

                first = fetch(0)
                total = first.get("total")
                provenance = {
                    key: first.get(key)
                    for key in ("_feed", "_as_of", "_excluded", "_snapshot_id", "_reported_total")
                }
                if (
                    isinstance(total, bool)
                    or not isinstance(total, int)
                    or not 0 <= total <= MAX_ROWS
                ):
                    raise ValueError(
                        "Equity provider universe exceeds the supported snapshot size or has no valid count"
                    )
                stopped = threading.Event()

                def read(offset):
                    if stopped.is_set():
                        with error_lock:
                            error = (
                                errors[0]
                                if errors
                                else ValueError("Equity provider snapshot interrupted")
                            )
                        raise error
                    try:
                        return fetch(offset)
                    except Exception as error:
                        with error_lock:
                            errors.append(error)
                        stopped.set()
                        raise

                rows = []
                seen = set()

                def append(page, offset):
                    values = page.get("quotes") if isinstance(page, dict) else None
                    if isinstance(page, dict) and any(
                        page.get(key) != value for key, value in provenance.items()
                    ):
                        raise ValueError(
                            "Equity provider source or snapshot changed during pagination"
                        )
                    expected = min(PAGE_SIZE, total - offset)
                    if (
                        not isinstance(values, list)
                        or len(values) != expected
                        or page.get("total") != total
                    ):
                        raise ValueError(
                            "Equity provider pagination changed or returned an incomplete page"
                        )
                    for value in values:
                        row = normalize(value)
                        identity = row["instrument"]["id"]
                        if self.market == "MY" and (
                            not identity.endswith(".KL") or row["currency"] not in (None, "MYR")
                        ):
                            raise ValueError(
                                "Malaysian provider returned an unexpected identity/currency"
                            )
                        if identity in seen:
                            raise ValueError(
                                "Equity provider repeated a security across snapshot pages"
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
                self.provenance = provenance
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


# Retain the existing constructor for callers of the US module.
USSnapshot = EquitySnapshot
universe = EquitySnapshot()


def screener(sector, min_cap, min_change, min_volume, sort, descending, offset, limit):
    return screen_universe(
        universe, "US", sector, min_cap, min_change, min_volume, sort, descending, offset, limit
    )


def screen_universe(
    snapshot, market, sector, min_cap, min_change, min_volume, sort, descending, offset, limit
):
    rows, fetched_at = snapshot.snapshot()
    page, total = page_rows(
        rows, sector, min_cap, min_change, min_volume, sort, descending, offset, limit
    )
    observations = [row["observed_at"] for row in page if row["observed_at"] is not None]
    result = common.result(
        copy.deepcopy(page),
        f"Yahoo Finance screener · {market} quote snapshot",
        f"{len(rows)} provider-listed {market} equities; not verified as every listed issuer. Ranked by displayed regular-session quote values.",
        total=total,
        offset=offset,
        limit=limit,
        market=market,
        observed_at=min(observations) if page and len(observations) == len(page) else None,
        limitations=[
            "Complete retrieved provider universe; no curated stock basket or minimum liquidity filter is imposed. US coverage may include OTC securities.",
            "Quotes may be delayed and observations are not simultaneous. Rankings use regular-session values, not pre/post-market changes. Observation time is the oldest quote on this page.",
        ],
    )
    if snapshot.provenance.get("_feed") == "nasdaq":
        result["source"] = "Nasdaq · broad US fallback"
        result["coverage"] = (
            f"{len(rows)} supported Nasdaq provider results; broad US fallback, not every US/OTC issuer"
        )
        result["limitations"] = [
            "Yahoo screener returned empty coverage; values come from the existing Nasdaq provider. No curated basket or liquidity filter is imposed.",
            "Exchange, ISO currency and precise per-security observation times are unavailable; quotes may be delayed.",
        ]
        if snapshot.provenance.get("_as_of"):
            result["limitations"].append(
                "Nasdaq reported data date: " + snapshot.provenance["_as_of"]
            )
        if snapshot.provenance.get("_excluded"):
            result["limitations"].append(
                f"{snapshot.provenance['_excluded']} provider identifiers requiring Yahoo mapping were omitted; identities were not guessed."
            )
    reported = snapshot.provenance.get("_reported_total")
    if reported is not None and reported != len(rows):
        result["limitations"].append(
            f"Provider regional count is {reported}; retrieved exchange partitions contain {len(rows)} securities. Counts may differ during provider updates."
        )
    result["fetched_at"] = fetched_at
    return result
