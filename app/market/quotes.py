import logging
import os
from datetime import UTC, datetime
from app.market import common
from app.market import transport

logger = logging.getLogger(__name__)


@transport.yahoo_bound
def quote(key):
    asset = common.instrument(key)
    if asset["asset_class"] == "crypto":
        rows = transport.cg("coins/markets", {"vs_currency": "usd", "ids": key.split(":", 1)[1]})
        if not rows:
            raise ValueError("Crypto asset unavailable")
        row = next((row for row in rows if row.get("id") == key.split(":", 1)[1]), None)
        if row is None:
            raise ValueError("Crypto provider did not verify requested asset ID")
        fields = {
            "price": common.decimal(row.get("current_price")),
            "market_cap": common.decimal(row.get("market_cap")),
            "volume": common.decimal(row.get("total_volume")),
            "high": common.decimal(row.get("high_24h")),
            "low": common.decimal(row.get("low_24h")),
            "change_percent": common.number(row.get("price_change_percentage_24h")),
            "currency": "USD",
            "instrument": common.instrument(key, row),
        }
        return common.result(
            fields,
            "CoinGecko",
            "Aggregated crypto market snapshot",
            observed_at=row.get("last_updated"),
        )
    source = "Yahoo Finance"
    try:
        _, info = common.ticker_info(asset["symbol"])
    except Exception:
        key = os.getenv("FMP_API_KEY", "")
        if not key:
            raise
        rows = transport.fetch(
            "https://financialmodelingprep.com/stable/quote",
            {"symbol": asset["symbol"], "apikey": key},
        )
        row = (
            next(
                (
                    row
                    for row in rows
                    if isinstance(row, dict) and row.get("symbol") == asset["symbol"]
                ),
                None,
            )
            if isinstance(rows, list)
            else None
        )
        if row is None:
            raise ValueError("FMP did not verify requested symbol")
        profile = {}
        try:
            profiles = transport.fetch(
                "https://financialmodelingprep.com/stable/profile",
                {"symbol": asset["symbol"], "apikey": key},
            )
            profile = next(
                (entry for entry in profiles if entry.get("symbol") == asset["symbol"]),
                {},
            )
        except Exception:  # noqa: BLE001 -- isolate third-party provider failures; expose partial coverage
            profile = {}
        info = {
            "symbol": asset["symbol"],
            "shortName": row.get("name"),
            "exchange": row.get("exchange"),
            "currency": profile.get("currency"),
            "regularMarketPrice": row.get("price"),
            "regularMarketTime": row.get("timestamp"),
            "regularMarketOpen": row.get("open"),
            "regularMarketDayHigh": row.get("dayHigh"),
            "regularMarketDayLow": row.get("dayLow"),
            "regularMarketPreviousClose": row.get("previousClose"),
            "regularMarketVolume": row.get("volume"),
            "marketCap": row.get("marketCap"),
            "regularMarketChangePercent": row.get("changePercentage"),
            "fiftyTwoWeekLow": row.get("yearLow"),
            "fiftyTwoWeekHigh": row.get("yearHigh"),
            "beta": profile.get("beta"),
        }
        source = "FMP"
    fields = {
        "instrument": common.instrument(asset["symbol"], info),
        "currency": info.get("currency"),
        "market_state": info.get("marketState"),
    }
    mapping = {
        "price": "regularMarketPrice",
        "bid": "bid",
        "ask": "ask",
        "open": "regularMarketOpen",
        "high": "regularMarketDayHigh",
        "low": "regularMarketDayLow",
        "previous_close": "regularMarketPreviousClose",
        "volume": "regularMarketVolume",
        "market_cap": "marketCap",
        "pe": "trailingPE",
        "eps": "trailingEps",
        "dividend_yield": "dividendYield",
        "week52_low": "fiftyTwoWeekLow",
        "week52_high": "fiftyTwoWeekHigh",
        "beta": "beta",
        "shares_outstanding": "sharesOutstanding",
        "change_percent": "regularMarketChangePercent",
    }
    fields.update({name: common.decimal(info.get(field)) for name, field in mapping.items()})
    observed = info.get("regularMarketTime")
    return common.result(
        fields,
        source,
        "Provider-dependent exchange coverage; quotes may be delayed",
        observed_at=datetime.fromtimestamp(observed, UTC).isoformat()
        if common.number(observed) is not None
        else None,
    )
