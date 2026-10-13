"""One exact-symbol Yahoo adapter shared by portfolio, research and native ingestion."""

import math
import re
from datetime import datetime, timezone
import pandas as pd
import yfinance as yf


def symbol(value):
    value = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9^][A-Z0-9.^=_\-]{0,31}", value):
        raise ValueError(
            "Use an exact Yahoo Finance ticker, including its exchange suffix"
        )
    return value


def refresh_statement_cache():
    """yfinance caches statement URLs for the whole UTC day without a TTL.

    Invalidate that response cache before a fresh statement retrieval. Keep this
    version-specific integration here; do not mutate ticker or transport instances.
    """
    from yfinance.data import YfData

    YfData.cache_get.cache_clear()


def ticker_info(value):
    value = symbol(value)
    ticker = yf.Ticker(value)
    info = ticker.get_info()
    if not isinstance(info, dict) or str(info.get("symbol", "")).upper() != value:
        raise ValueError(
            f"Yahoo did not verify exact symbol {value}; choose a ticker from symbol search"
        )
    return ticker, info


def search_symbols(query, count=8):
    return [
        {
            "symbol": q["symbol"],
            "name": q.get("shortname") or q.get("longname") or q["symbol"],
            "exchange": q.get("exchDisp") or q.get("exchange"),
            "type": q.get("quoteType"),
        }
        for q in yf.Search(
            query, max_results=count, news_count=0, lists_count=0, timeout=10
        ).quotes
        if isinstance(q, dict) and q.get("symbol")
    ][:count]


def quote(value):
    value = symbol(value)
    _, info = ticker_info(value)
    price = info.get("regularMarketPrice")
    currency = info.get("currency")
    if (
        not isinstance(price, (int, float))
        or isinstance(price, bool)
        or not math.isfinite(price)
        or price <= 0
    ):
        raise ValueError(f"Quote unavailable for {value}")
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError(f"Quote currency unavailable or unsupported for {value}")
    previous = info.get("regularMarketPreviousClose")
    change = (
        (price / previous - 1) * 100
        if isinstance(previous, (int, float))
        and not isinstance(previous, bool)
        and previous > 0
        and math.isfinite(previous)
        else None
    )
    return {
        "symbol": value,
        "name": info.get("shortName") or info.get("longName") or value,
        "price": str(price),
        "currency": currency,
        "exchange": info.get("exchange"),
        "sector": info.get("sector") or "Unclassified",
        "market_state": info.get("marketState") or "Unknown",
        "as_of": info.get("regularMarketTime"),
        "change_percent": change,
        "source": "Yahoo Finance",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def price_series(symbols, currencies, period):
    symbols = list(dict.fromkeys(symbol(s) for s in symbols))
    data = yf.download(
        symbols,
        period=period,
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        progress=False,
        threads=False,
        timeout=10,
    )
    result = []
    for value in symbols:
        try:
            closes = (
                data[value]["Close"]
                if isinstance(data.columns, pd.MultiIndex)
                else data["Close"]
                if len(symbols) == 1
                else None
            )
            if closes is None:
                raise ValueError()
            closes = pd.to_numeric(closes, errors="raise").dropna()
            if (
                closes.empty
                or closes.index.has_duplicates
                or any(not math.isfinite(float(p)) or p <= 0 for p in closes)
            ):
                raise ValueError()
            result.append(
                {
                    "symbol": value,
                    "currency": currencies[value],
                    "prices": [
                        {"date": pd.Timestamp(day).date().isoformat(), "close": str(p)}
                        for day, p in closes.sort_index().items()
                    ],
                }
            )
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"Valid price history unavailable for {value}") from None
    return result
