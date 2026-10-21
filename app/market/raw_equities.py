"""Bounded Yahoo transport fields for native equity snapshot processing."""

import math

from yfinance.exceptions import YFRateLimitError

from app.market import common, malaysia, transport, us

FIELDS = (
    "symbol",
    "shortName",
    "longName",
    "quoteType",
    "exchange",
    "exchDisp",
    "currency",
    "sector",
)


def project(info):
    """Keep the wire payload finite and small without calculating display values."""
    row = {key: info.get(key) for key in FIELDS}
    row["shortName"] = info.get("shortName") or info.get("shortname")
    row["longName"] = info.get("longName") or info.get("longname")
    price = info.get("regularMarketPrice")
    row["regularMarketPrice"] = (
        str(price)
        if isinstance(price, (int, float)) and not isinstance(price, bool) and math.isfinite(price)
        else None
    )
    if isinstance(price, str):
        row["regularMarketPrice"] = price
    for key in ("marketCap", "regularMarketVolume", "regularMarketChangePercent"):
        row[key] = common.number(info.get(key))
    timestamp = common.number(info.get("regularMarketTime"))
    row["regularMarketTime"] = (
        int(timestamp) if timestamp is not None and timestamp.is_integer() else None
    )
    return row


def us_page(offset: int):
    return equity_page(offset, "US")


def my_page(offset: int):
    return equity_page(offset, "MY")


def equity_page(offset: int, market: str):
    response = us.fetch_page(offset) if market == "US" else us.fetch_page(offset, "my")
    values = response.get("quotes")
    if not isinstance(values, list):
        raise ValueError("US provider page has no quote array")
    return common.result(
        {
            "total": response.get("total"),
            "quotes": [project(row) for row in values],
            "feed": response.get("_feed", "yahoo"),
            "as_of": response.get("_as_of"),
            "excluded": response.get("_excluded", 0),
            "snapshot_id": response.get("_snapshot_id"),
            "reported_total": response.get("_reported_total"),
        },
        "Nasdaq" if response.get("_feed") == "nasdaq" else "Yahoo Finance",
        f"Complete provider-listed {market} equity page; no tracked-set restriction",
    )


def my_symbols():
    return common.result(
        list(malaysia.symbols()), "Local configuration", "Configured Malaysian tracked set"
    )


@transport.yahoo_bound
def equity_info(instrument: str):
    if not instrument.startswith("yahoo:") or not instrument.endswith(".KL"):
        raise ValueError("Native tracked-set transport requires an exact Yahoo .KL identity")
    try:
        _, info = common.ticker_info(instrument.removeprefix("yahoo:"))
    except YFRateLimitError as error:
        raise transport.Throttled(60) from error
    return common.result(project(info), "Yahoo Finance", "Exact-symbol provider quote")
