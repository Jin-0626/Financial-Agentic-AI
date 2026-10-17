import logging
import math
import re
from datetime import UTC, datetime
from numbers import Real
from app.infrastructure.providers import symbol as symbol, ticker_info as ticker_info

logger = logging.getLogger(__name__)

RANGES = {
    "1D": ("1d", "5m"),
    "5D": ("5d", "15m"),
    "1M": ("1mo", "1d"),
    "6M": ("6mo", "1d"),
    "YTD": ("ytd", "1d"),
    "1Y": ("1y", "1d"),
    "5Y": ("5y", "1wk"),
    "MAX": ("max", "1mo"),
}


def now():
    return datetime.now(UTC).isoformat()


def number(value):
    return (
        float(value)
        if isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)
        else None
    )


def decimal(value):
    value = number(value)
    return str(value) if value is not None else None


def instrument(value, info=None):
    if value.startswith("coingecko:"):
        slug = value.split(":", 1)[1]
        if not re.fullmatch(r"[a-z0-9-]{1,100}", slug):
            raise ValueError("Invalid crypto asset ID")
        return {
            "id": value,
            "symbol": (info or {}).get("symbol", slug).upper(),
            "name": (info or {}).get("name", slug),
            "asset_class": "crypto",
            "exchange": "CoinGecko",
        }
    value = symbol(value.removeprefix("yahoo:"))
    info = info or {}
    return {
        "id": "yahoo:" + value,
        "symbol": value,
        "name": info.get("shortName")
        or info.get("shortname")
        or info.get("longname")
        or info.get("longName")
        or value,
        "asset_class": (info.get("quoteType") or "equity").lower(),
        "exchange": info.get("exchDisp") or info.get("exchange") or None,
    }


def result(data, source, coverage, **metadata):
    return {
        "status": "ok",
        "data": data,
        "source": source,
        "fetched_at": now(),
        "observed_at": metadata.pop("observed_at", None),
        "coverage": coverage,
        "stale": False,
        **metadata,
    }
