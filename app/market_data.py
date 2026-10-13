"""Bounded public-market adapters for the Financial terminal; no portfolio writes."""

import math
import os
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from numbers import Real
from urllib.parse import urlsplit

import requests
import yfinance as yf

from .providers import symbol, ticker_info

HOSTS = {
    "api.coingecko.com",
    "api.nasdaq.com",
    "api.stlouisfed.org",
    "api.bnm.gov.my",
    "nfs.faireconomy.media",
    "www.ecb.europa.eu",
    "financialmodelingprep.com",
}
_HTTP_COOLDOWN = {}
_HTTP_LOCK = threading.Lock()
_HTTP_SLOTS = {host: threading.BoundedSemaphore(4) for host in HOSTS}
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
        if isinstance(value, Real)
        and not isinstance(value, bool)
        and math.isfinite(value)
        else None
    )


def decimal(value):
    value = number(value)
    return str(value) if value is not None else None


class Throttled(RuntimeError):
    def __init__(self, seconds=60):
        self.seconds = max(1, min(seconds, 300))
        super().__init__("Provider rate limit reached")


def fetch(url, params=None, headers=None):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in HOSTS
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Provider destination is not allowed")
    with _HTTP_SLOTS[parsed.hostname], requests.Session() as session:
        with _HTTP_LOCK:
            remaining = _HTTP_COOLDOWN.get(parsed.hostname, 0) - time.monotonic()
        if remaining > 0:
            raise Throttled(math.ceil(remaining))
        with session.get(
            url,
            params=params,
            headers=headers,
            timeout=(5, 15),
            stream=True,
            allow_redirects=False,
        ) as response:
            if response.status_code == 429:
                retry = response.headers.get("Retry-After", "60")
                try:
                    seconds = int(retry)
                except ValueError:
                    try:
                        seconds = int(
                            (
                                parsedate_to_datetime(retry) - datetime.now(UTC)
                            ).total_seconds()
                        )
                    except (ValueError, TypeError):
                        seconds = 60
                error = Throttled(seconds)
                with _HTTP_LOCK:
                    _HTTP_COOLDOWN[parsed.hostname] = time.monotonic() + error.seconds
                raise error
            response.raise_for_status()
            if 300 <= response.status_code < 400:
                raise ValueError("Provider redirect is not allowed")
            body = bytearray()
            for chunk in response.iter_content(65536):
                body.extend(chunk)
                if len(body) > 8 * 1024 * 1024:
                    raise ValueError("Provider response exceeds limit")
            import json

            return json.loads(body)


_YAHOO_SLOTS = threading.BoundedSemaphore(4)


def yahoo_bound(fn):
    from functools import wraps

    @wraps(fn)
    def guarded(*args, **kwargs):
        with _YAHOO_SLOTS:
            return fn(*args, **kwargs)

    return guarded


def cg(path, params=None):
    key = os.getenv("COINGECKO_DEMO_API_KEY", "")
    return fetch(
        "https://api.coingecko.com/api/v3/" + path,
        params,
        {"x-cg-demo-api-key": key} if key else {},
    )


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


@yahoo_bound
def search(query):
    found, failures = [], []
    try:
        for item in yf.Search(
            query, max_results=20, news_count=0, lists_count=0, timeout=10
        ).quotes:
            if isinstance(item, dict) and item.get("symbol"):
                found.append(instrument(item["symbol"], item))
    except Exception:  # noqa: BLE001 -- isolate third-party provider failures; expose partial coverage
        failures.append("Yahoo symbol search unavailable")
    try:
        for coin in cg("search", {"query": query}).get("coins", [])[:10]:
            found.append(instrument("coingecko:" + coin["id"], coin))
    except Exception:  # noqa: BLE001 -- isolate third-party provider failures; expose partial coverage
        failures.append("Crypto search unavailable")
    if not found and len(failures) == 2:
        raise RuntimeError("Symbol providers unavailable")
    return result(
        list({item["id"]: item for item in found}.values())[:25],
        "Yahoo Finance / CoinGecko",
        "Global provider search; exchange identity retained",
        limitations=failures,
    )


@yahoo_bound
def quote(key):
    asset = instrument(key)
    if asset["asset_class"] == "crypto":
        rows = cg("coins/markets", {"vs_currency": "usd", "ids": key.split(":", 1)[1]})
        if not rows:
            raise ValueError("Crypto asset unavailable")
        row = next((row for row in rows if row.get("id") == key.split(":", 1)[1]), None)
        if row is None:
            raise ValueError("Crypto provider did not verify requested asset ID")
        fields = {
            "price": decimal(row.get("current_price")),
            "market_cap": decimal(row.get("market_cap")),
            "volume": decimal(row.get("total_volume")),
            "high": decimal(row.get("high_24h")),
            "low": decimal(row.get("low_24h")),
            "change_percent": number(row.get("price_change_percentage_24h")),
            "currency": "USD",
            "instrument": instrument(key, row),
        }
        return result(
            fields,
            "CoinGecko",
            "Aggregated crypto market snapshot",
            observed_at=row.get("last_updated"),
        )
    source = "Yahoo Finance"
    try:
        _, info = ticker_info(asset["symbol"])
    except Exception:
        key = os.getenv("FMP_API_KEY", "")
        if not key:
            raise
        rows = fetch(
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
            profiles = fetch(
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
        "instrument": instrument(asset["symbol"], info),
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
    fields.update({name: decimal(info.get(field)) for name, field in mapping.items()})
    observed = info.get("regularMarketTime")
    return result(
        fields,
        source,
        "Provider-dependent exchange coverage; quotes may be delayed",
        observed_at=datetime.fromtimestamp(observed, UTC).isoformat()
        if number(observed) is not None
        else None,
    )


@yahoo_bound
def ohlcv(key, range_key):
    asset = instrument(key)
    if asset["asset_class"] == "crypto":
        days = {
            "1D": 1,
            "5D": 7,
            "1M": 30,
            "6M": 180,
            "YTD": 365,
            "1Y": 365,
            "5Y": 1825,
            "MAX": "max",
        }[range_key]
        rows = cg(
            "coins/" + key.split(":", 1)[1] + "/ohlc",
            {"vs_currency": "usd", "days": days},
        )
        candles = [
            {
                "time": int(row[0] / 1000),
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
                "volume": None,
            }
            for row in rows
        ]
        if range_key == "5D":
            candles = [
                c
                for c in candles
                if c["time"] >= int((datetime.now(UTC) - timedelta(days=5)).timestamp())
            ]
        if range_key == "YTD":
            candles = [
                c
                for c in candles
                if c["time"]
                >= int(datetime(datetime.now(UTC).year, 1, 1, tzinfo=UTC).timestamp())
            ]
        currency = "USD"
        source = "CoinGecko"
        interval = "provider-selected"
    else:
        ticker, info = ticker_info(asset["symbol"])
        period, interval = RANGES[range_key]
        frame = ticker.history(
            period=period,
            interval=interval,
            auto_adjust=False,
            actions=False,
            timeout=10,
        )
        candles = []
        for timestamp, row in frame.iterrows():
            record = {
                name: number(row.get(column))
                for name, column in (
                    ("open", "Open"),
                    ("high", "High"),
                    ("low", "Low"),
                    ("close", "Close"),
                    ("volume", "Volume"),
                )
            }
            if any(
                record[name] is None or record[name] <= 0
                for name in ("open", "high", "low", "close")
            ):
                continue
            if timestamp.tzinfo is None:
                zone = info.get("exchangeTimezoneName")
                if not zone:
                    raise ValueError("History timezone unavailable")
                timestamp = timestamp.tz_localize(zone)
            if record["volume"] is not None and record["volume"] < 0:
                record["volume"] = None
            record["time"] = int(timestamp.timestamp())
            record["session"] = timestamp.strftime("%Y-%m-%d")
            candles.append(record)
        currency = info.get("currency")
        source = "Yahoo Finance"
    original_count = len(candles)
    candles = [
        row
        for row in candles
        if all(
            number(row.get(name)) is not None and row[name] > 0
            for name in ("open", "high", "low", "close")
        )
        and row["high"] >= max(row["open"], row["close"], row["low"])
        and row["low"] <= min(row["open"], row["close"], row["high"])
        and 0 < row["time"] < 253402300800
    ]
    invalid_count = original_count - len(candles)
    candles = sorted(
        {row["time"]: row for row in candles}.values(), key=lambda row: row["time"]
    )[-10000:]
    return result(
        candles,
        source,
        "Unadjusted display OHLC; separate from adjusted portfolio history",
        currency=currency,
        instrument=asset,
        interval=interval,
        range=range_key,
        observed_at=datetime.fromtimestamp(candles[-1]["time"], UTC).isoformat()
        if candles
        else None,
        limitations=(["Volume unavailable"] if asset["asset_class"] == "crypto" else [])
        + (
            [
                f"Excluded {invalid_count} invalid provider bars; missing bars were not filled"
            ]
            if invalid_count
            else []
        ),
    )


@yahoo_bound
def news(key=None):
    from urllib.parse import parse_qsl, urlencode, urlunsplit

    from .financial_tools import _public_market_news

    asset = instrument(key) if key else None
    articles = []
    failures = []
    try:
        articles.extend(_public_market_news(20, asset["symbol"] if asset else None, 7))
    except Exception:  # noqa: BLE001 -- isolate third-party provider failures; expose partial coverage
        failures.append("RSS feed unavailable")
    fmp = os.getenv("FMP_API_KEY", "")
    if fmp:
        try:
            raw = fetch(
                "https://financialmodelingprep.com/stable/news/stock"
                if asset
                else "https://financialmodelingprep.com/stable/news/stock-latest",
                {
                    "apikey": fmp,
                    "limit": 20,
                    **({"symbols": asset["symbol"]} if asset else {}),
                },
            )
            if not isinstance(raw, list):
                raise TypeError("Invalid FMP news response")
            articles.extend(
                {
                    "title": r.get("title"),
                    "url": r.get("url"),
                    "provider": r.get("publisher") or r.get("site"),
                    "pub_date": r.get("publishedDate"),
                }
                for r in raw[:20]
            )
        except Exception:  # noqa: BLE001 -- isolate third-party provider failures; expose partial coverage
            failures.append("FMP news unavailable")
    if asset and asset["asset_class"] != "crypto":
        try:
            for item in yf.Ticker(asset["symbol"]).get_news(count=20):
                item = item.get("content", item)
                articles.append(
                    {
                        "title": item.get("title"),
                        "url": (item.get("canonicalUrl") or {}).get("url"),
                        "provider": (item.get("provider") or {}).get("displayName"),
                        "pub_date": item.get("pubDate"),
                    }
                )
        except Exception:  # noqa: BLE001 -- isolate third-party provider failures; expose partial coverage
            failures.append("Yahoo news unavailable")
    seen_urls, seen_titles = set(), set()
    unique = []
    for article in articles:
        url = article.get("url")
        title = article.get("title")
        if not isinstance(url, str) or not isinstance(title, str):
            continue
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        clean = urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(
                    [
                        (k, v)
                        for k, v in parse_qsl(parsed.query)
                        if not k.startswith("utm_")
                    ]
                ),
                "",
            )
        )
        headline = re.sub(r"\W+", " ", title).strip().casefold()
        if clean in seen_urls or headline in seen_titles:
            continue
        seen_urls.add(clean)
        seen_titles.add(headline)
        unique.append(
            {
                "title": title,
                "url": clean,
                "publisher": article.get("provider"),
                "published_at": article.get("pub_date"),
            }
        )
    if not unique:
        return {
            **result(
                [],
                "Public RSS / Yahoo Finance / FMP",
                "No verified current articles returned",
                limitations=failures,
            ),
            "status": "unavailable" if failures else "ok",
        }
    return result(
        unique[:30],
        "Public RSS / Yahoo Finance / FMP",
        "Provider headlines; publication dates retained",
        limitations=failures,
    )


@yahoo_bound
def screener(
    market, sector, min_cap, min_change, min_volume, sort, descending, offset, limit
):
    fields = {
        "market_cap": "intradaymarketcap",
        "change_percent": "percentchange",
        "volume": "dayvolume",
        "symbol": "ticker",
        "price": "intradayprice",
    }
    queries = [yf.EquityQuery("eq", ["region", "my" if market == "MY" else "us"])]
    if sector:
        queries.append(yf.EquityQuery("eq", ["sector", sector]))
    for field, value in (
        ("intradaymarketcap", min_cap),
        ("percentchange", min_change),
        ("dayvolume", min_volume),
    ):
        if value is not None:
            queries.append(yf.EquityQuery("gte", [field, value]))
    query = queries[0] if len(queries) == 1 else yf.EquityQuery("and", queries)
    raw = yf.screen(
        query, offset=offset, size=limit, sortField=fields[sort], sortAsc=not descending
    )
    rows = []
    for item in raw.get("quotes", []):
        if not item.get("symbol"):
            continue
        rows.append(
            {
                "instrument": instrument(item["symbol"], item),
                "currency": item.get("currency"),
                "sector": item.get("sector") or "Unclassified",
                "market_cap": number(item.get("marketCap")),
                "price": decimal(item.get("regularMarketPrice")),
                "volume": number(item.get("regularMarketVolume")),
                "change_percent": number(item.get("regularMarketChangePercent")),
            }
        )
    return result(
        rows,
        "Yahoo Finance screener",
        "Provider-listed "
        + market
        + " equity universe; not verified as every listed issuer",
        total=raw.get("total"),
        offset=offset,
        limit=limit,
        market=market,
    )


def nasdaq_universe():
    raw = fetch(
        "https://api.nasdaq.com/api/screener/stocks",
        {"tableonly": "true", "limit": 10000, "offset": 0},
        {"User-Agent": "Financial-Deep-Agents/1.0", "Accept": "application/json"},
    )
    rows = ((raw.get("data") or {}).get("table") or {}).get("rows", [])
    return result(
        [
            {
                "symbol": row.get("symbol"),
                "name": row.get("name"),
                "sector": row.get("sector"),
            }
            for row in rows[:10000]
        ],
        "Nasdaq",
        "Provider directory snapshot; endpoint-dependent US coverage",
        total=(raw.get("data") or {}).get("totalrecords"),
    )


@yahoo_bound
def options(key, expiry=None):
    asset = instrument(key)
    if asset["asset_class"] == "crypto" or asset["symbol"].endswith(".KL"):
        return {
            **result(
                [],
                "Yahoo Finance",
                "No supported listed-option coverage for this instrument",
            ),
            "status": "unavailable",
        }
    ticker = yf.Ticker(asset["symbol"])
    expiries = list(ticker.options)
    if not expiries:
        return {
            **result([], "Yahoo Finance", "Provider returned no option expiries"),
            "status": "unavailable",
        }
    selected = expiry or expiries[0]
    if selected not in expiries:
        raise ValueError("Expiry is not available for this instrument")
    chain = ticker.option_chain(selected)
    mapping = {
        "contract": "contractSymbol",
        "strike": "strike",
        "bid": "bid",
        "ask": "ask",
        "last": "lastPrice",
        "volume": "volume",
        "open_interest": "openInterest",
        "implied_volatility": "impliedVolatility",
    }

    def records(frame):
        return [
            {
                **{
                    key: (
                        str(row[field])
                        if key == "contract"
                        else decimal(row.get(field))
                    )
                    for key, field in mapping.items()
                },
                "itm": bool(row.get("inTheMoney", False)),
                "observed_at": row["lastTradeDate"].isoformat()
                if hasattr(row.get("lastTradeDate"), "isoformat")
                else None,
            }
            for _, row in frame.head(1000).iterrows()
        ]

    return result(
        {
            "calls": records(chain.calls),
            "puts": records(chain.puts),
            "expiries": expiries,
            "expiry": selected,
        },
        "Yahoo Finance",
        "Listed options where supplied; quotes may be delayed",
        instrument=asset,
    )


def crypto():
    rows = cg(
        "coins/markets",
        {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 30,
            "page": 1,
            "sparkline": "true",
        },
    )
    global_data = cg("global").get("data", {})
    return result(
        [
            {
                "instrument": instrument("coingecko:" + row["id"], row),
                "price": decimal(row.get("current_price")),
                "market_cap": number(row.get("market_cap")),
                "change_percent": number(row.get("price_change_percentage_24h")),
                "sparkline": ((row.get("sparkline_in_7d") or {}).get("price") or [])[
                    -168:
                ],
                "observed_at": row.get("last_updated"),
            }
            for row in rows
        ],
        "CoinGecko",
        "Top 30 assets by provider market cap",
        currency="USD",
        dominance={
            key: global_data.get("market_cap_percentage", {}).get(key)
            for key in ("btc", "eth")
        },
    )


YIELDS = {
    "1M": "DGS1MO",
    "3M": "DGS3MO",
    "6M": "DGS6MO",
    "1Y": "DGS1",
    "2Y": "DGS2",
    "5Y": "DGS5",
    "10Y": "DGS10",
    "30Y": "DGS30",
}


def fred_series(series_id, vintage=None):
    key = os.getenv("FRED_API_KEY", "")
    if not key:
        raise ValueError("FRED_API_KEY is required for official release observations")
    return fetch(
        "https://api.stlouisfed.org/fred/series/observations",
        {
            "series_id": series_id,
            "api_key": key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 24,
            **({"realtime_start": vintage, "realtime_end": vintage} if vintage else {}),
        },
    ).get("observations", [])


def macro():
    rows = []
    limitations = []
    for label, ticker in (
        ("VIX", "^VIX"),
        ("S&P 500", "^GSPC"),
        ("Bursa KLCI", "^KLSE"),
        ("Gold proxy", "GLD"),
        ("Oil proxy", "USO"),
    ):
        try:
            value = quote("yahoo:" + ticker)
            rows.append(
                {
                    "name": label,
                    "kind": "index" if ticker.startswith("^") else "ETF proxy",
                    **value["data"],
                    "source": value["source"],
                    "observed_at": value["observed_at"],
                }
            )
        except Exception:  # noqa: BLE001 -- isolate third-party provider failures; expose partial coverage
            limitations.append(label + " unavailable")
    for maturity, series_id in YIELDS.items():
        if not os.getenv("FRED_API_KEY"):
            break
        try:
            observation = next(
                (row for row in fred_series(series_id) if row.get("value") != "."), None
            )
            if observation:
                rows.append(
                    {
                        "name": "Treasury " + maturity,
                        "kind": "yield",
                        "price": observation["value"],
                        "unit": "percent",
                        "observed_at": observation["date"],
                        "source": "FRED / US Treasury",
                        "series_id": series_id,
                    }
                )
        except Exception:  # noqa: BLE001 -- isolate third-party provider failures; expose partial coverage
            limitations.append("Yield " + maturity + " unavailable")
    if not os.getenv("FRED_API_KEY"):
        limitations.append(
            "Treasury yield curve requires FRED_API_KEY; fiscal interest records are not yield quotes"
        )
    try:
        raw = fetch(
            "https://api.bnm.gov.my/public/opr",
            headers={"Accept": "application/vnd.BNM.API.v1+json"},
        )
        values = raw.get("data", [])
        values = values if isinstance(values, list) else [values]
        values = sorted(
            (row for row in values if isinstance(row, dict)),
            key=lambda row: str(row.get("date") or ""),
        )
        for row in values[-1:]:
            rows.append(
                {
                    "name": "Malaysia OPR",
                    "kind": "policy rate",
                    "price": str(row.get("rate", row.get("opr")))
                    if row.get("rate", row.get("opr")) is not None
                    else None,
                    "unit": "percent",
                    "observed_at": row.get("date"),
                    "source": "Bank Negara Malaysia",
                }
            )
    except Exception:  # noqa: BLE001 -- isolate third-party provider failures; expose partial coverage
        limitations.append("BNM OPR unavailable")
    return result(
        rows,
        "Yahoo Finance / FRED / BNM",
        "Dated index snapshots, ETF proxies and official daily/policy observations",
        limitations=limitations,
    )


def calendar_series(row):
    title = str(row.get("title", "")).lower()
    country = row.get("country")
    if country == "USD" and "cpi" in title:
        return (
            "CPILFESL" if "core" in title else "CPIAUCSL",
            "inflation",
            "US Bureau of Labor Statistics",
        )
    if country == "USD" and ("non-farm" in title or "nonfarm" in title):
        return ("PAYEMS", "change", "US Bureau of Labor Statistics")
    if country == "USD" and "federal funds" in title:
        return ("DFEDTARU", "daily", "Board of Governors of the Federal Reserve System")
    if country == "EUR" and "refinancing" in title:
        return ("ECBMRRFR", "daily", "European Central Bank")
    if country == "EUR" and "deposit facility" in title:
        return ("ECBDFR", "daily", "European Central Bank")
    return None


def verified_calendar_actual(row, observations):
    """Only derive an outcome when the feed identifies its reference period."""
    period = row.get("reference_period")
    spec = calendar_series(row)
    if not spec or not isinstance(period, str):
        return None
    series_id, method, publisher = spec
    pattern = r"\d{4}-\d{2}-\d{2}" if method == "daily" else r"\d{4}-\d{2}"
    if not re.fullmatch(pattern, period):
        return None
    title = str(row.get("title", "")).lower()
    target = next(
        (
            obs
            for obs in observations
            if (
                obs.get("date") == period
                if method == "daily"
                else str(obs.get("date", "")).startswith(period + "-")
            )
            and obs.get("value") != "."
        ),
        None,
    )
    if target is None:
        return None
    try:
        year, month = map(int, period[:7].split("-"))
        datetime(year, month, 1)
        if method == "daily":
            datetime.fromisoformat(period)
            value = Decimal(target["value"])
            units = "percent"
        else:
            if method == "inflation" and "y/y" in title:
                previous = f"{year - 1:04d}-{month:02d}"
            elif method == "change" or "m/m" in title:
                previous = (
                    f"{year - 1:04d}-12"
                    if month == 1
                    else f"{year:04d}-{month - 1:02d}"
                )
            else:
                return None
            base = next(
                (
                    obs
                    for obs in observations
                    if str(obs.get("date", "")).startswith(previous + "-")
                    and obs.get("value") != "."
                ),
                None,
            )
            if base is None:
                return None
            value = (
                Decimal(target["value"]) - Decimal(base["value"])
                if method == "change"
                else (Decimal(target["value"]) / Decimal(base["value"]) - 1) * 100
            )
            units = "thousand persons" if method == "change" else "percent"
        if not value.is_finite():
            return None
        formatted = format(value.quantize(Decimal("0.1")), "f") + (
            "%" if units == "percent" else " thousand persons"
        )
        return {
            "value": formatted,
            "units": units,
            "reference_period": period,
            "source": "FRED / " + publisher,
            "series_id": series_id,
            "url": "https://fred.stlouisfed.org/series/" + series_id,
        }
    except (ValueError, InvalidOperation, ZeroDivisionError, TypeError, KeyError):
        return None


def economic_calendar():
    rows = fetch("https://nfs.faireconomy.media/ff_calendar_thisweek.json")
    if not isinstance(rows, list):
        raise TypeError("Invalid economic calendar response")
    events = []
    observations = {}
    for row in rows[:500]:
        outcome = None
        when = row.get("date", "")
        try:
            released = datetime.fromisoformat(when) <= datetime.now(UTC)
        except (ValueError, TypeError):
            released = False
        spec = calendar_series(row)
        if (
            released
            and row.get("reference_period")
            and spec
            and os.getenv("FRED_API_KEY")
        ):
            series_id = spec[0]
            vintage = when[:10]
            key = (series_id, vintage)
            if key not in observations:
                try:
                    observations[key] = fred_series(series_id, vintage)
                except Exception:  # noqa: BLE001 -- isolate third-party provider failures; expose partial coverage
                    observations[key] = []
            outcome = verified_calendar_actual(row, observations[key])
            if outcome:
                outcome["vintage_date"] = vintage
        events.append(
            {
                "title": row.get("title"),
                "country": row.get("country"),
                "date": row.get("date"),
                "impact": row.get("impact"),
                "forecast": row.get("forecast") or None,
                "previous": row.get("previous") or None,
                "actual": outcome["value"] if outcome else None,
                "actual_evidence": outcome,
                "actual_status": "Reference-period-matched official observation"
                if outcome
                else "No event-matched release observation verified",
            }
        )
    return result(
        events,
        "Forex Factory public calendar / FRED where matched",
        "Current-week public schedule; actual outcomes require event-matched official evidence",
        limitations=[
            "Malaysian release coverage may be absent; many events lack the reference period needed for safe actual matching"
        ],
    )


@yahoo_bound
def earnings(key):
    asset = instrument(key)
    if asset["asset_class"] == "crypto":
        return {
            **result([], "Yahoo Finance", "Earnings do not apply to crypto"),
            "status": "unavailable",
        }
    ticker = yf.Ticker(asset["symbol"])
    frame = ticker.get_earnings_dates(limit=12)
    if frame is not None:
        frame = frame.sort_index(ascending=False).head(12)
    rows = []
    history = None
    if frame is not None and not frame.empty:
        verified = [
            when
            for when in frame.index
            if getattr(when, "tzinfo", None) is not None
            and (when.hour or when.minute)
            and when.to_pydatetime() < datetime.now(UTC)
        ]
        if verified:
            try:
                history = ticker.history(
                    start=(min(verified) - timedelta(days=7)).date().isoformat(),
                    end=datetime.now(UTC).date().isoformat(),
                    auto_adjust=True,
                    actions=False,
                    timeout=10,
                )
            except Exception:  # noqa: BLE001 -- isolate third-party provider failures; expose partial coverage
                history = None
        for when, row in frame.iterrows():
            move = None
            reason = "Announcement timing and next trading session not verified"
            if when in verified and history is not None and not history.empty:
                local = (
                    when.tz_convert(history.index.tz)
                    if history.index.tz is not None
                    else None
                )
                if local is not None:
                    bars = sorted(
                        (stamp.date(), number(price))
                        for stamp, price in history["Close"].items()
                        if number(price) is not None and price > 0
                    )
                    next_index = next(
                        (i for i, (day, _) in enumerate(bars) if day > local.date()),
                        None,
                    )
                    if next_index is not None and next_index > 0:
                        move = (bars[next_index][1] / bars[next_index - 1][1] - 1) * 100
                        reason = "Adjusted close-to-close return for first trading date after reported announcement date; not a causal attribution"
            rows.append(
                {
                    "date": when.isoformat(),
                    "eps_estimate": number(row.get("EPS Estimate")),
                    "eps_actual": number(row.get("Reported EPS")),
                    "surprise_percent": number(row.get("Surprise(%)")),
                    "next_session_move_percent": move,
                    "move_status": reason,
                }
            )
    return result(
        rows,
        "Yahoo Finance",
        "Provider earnings records; Bursa coverage and release timing may be incomplete",
        instrument=asset,
    )
