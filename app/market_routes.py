"""Authenticated read-only market endpoints; portfolio persistence stays separate."""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from . import market_data as data
from .auth import authenticated_identity
from .market_service import market_service

router = APIRouter(prefix="/market", dependencies=[Depends(authenticated_identity)])
Instrument = Annotated[
    str,
    Query(
        min_length=1,
        max_length=128,
        pattern=r"^(?:yahoo:[A-Za-z0-9.^=_-]+|coingecko:[a-z0-9-]+)$",
    ),
]


async def cached(name, provider, ttl, fn, *args):
    return await market_service.get((name, *args), provider, ttl, lambda: fn(*args))


@router.get("/capabilities")
async def capabilities():
    return {
        "widgets": [
            "chart",
            "quote",
            "watchlist",
            "news",
            "screener",
            "heatmap",
            "options",
            "crypto",
            "macro",
            "portfolio",
            "calendar",
        ],
        "markets": ["MY", "US", "global search", "crypto"],
        "coverage": "Provider-dependent; unavailable data is explicit",
        "ranges": list(data.RANGES),
        "treasury_requires": "FRED_API_KEY",
        "crypto_optional_key": "COINGECKO_DEMO_API_KEY",
    }


@router.get("/search")
async def search(query: Annotated[str, Query(min_length=1, max_length=80)]):
    if not query.strip():
        raise HTTPException(422, "Search query must not be blank")
    return await cached("search", "yahoo", 300, data.search, query.strip())


@router.get("/quote")
async def quote(instrument: Instrument):
    return await cached(
        "quote",
        "crypto" if instrument.startswith("coingecko:") else "yahoo",
        15,
        data.quote,
        instrument,
    )


@router.get("/ohlcv")
async def history(
    instrument: Instrument,
    range: Literal["1D", "5D", "1M", "6M", "YTD", "1Y", "5Y", "MAX"] = "6M",
):
    return await cached(
        "ohlcv",
        "crypto" if instrument.startswith("coingecko:") else "yahoo",
        60 if range in {"1D", "5D"} else 300,
        data.ohlcv,
        instrument,
        range,
    )


@router.get("/news")
async def news(instrument: Instrument | None = None):
    return await cached("news", "news", 120, data.news, instrument)


@router.get("/screener")
async def screener(
    market: Literal["MY", "US"] = "MY",
    sector: Annotated[str | None, Query(max_length=80)] = None,
    min_cap: Annotated[float | None, Query(ge=0, allow_inf_nan=False)] = None,
    min_change: Annotated[float | None, Query(allow_inf_nan=False)] = None,
    min_volume: Annotated[int | None, Query(ge=0)] = None,
    sort: Literal[
        "market_cap", "change_percent", "volume", "symbol", "price"
    ] = "market_cap",
    descending: bool = True,
    offset: Annotated[int, Query(ge=0, le=20000)] = 0,
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
):
    return await cached(
        "screener",
        "yahoo",
        60,
        data.screener,
        market,
        sector,
        min_cap,
        min_change,
        min_volume,
        sort,
        descending,
        offset,
        limit,
    )


@router.get("/universe/us")
async def universe():
    return await cached("universe", "nasdaq", 3600, data.nasdaq_universe)


@router.get("/options")
async def options(instrument: Instrument, expiry: date | None = None):
    return await cached(
        "options",
        "yahoo",
        30,
        data.options,
        instrument,
        expiry.isoformat() if expiry else None,
    )


@router.get("/crypto")
async def crypto():
    return await cached("crypto", "crypto", 30, data.crypto)


@router.get("/macro")
async def macro():
    return await cached("macro", "macro", 60, data.macro)


@router.get("/calendar/economic")
async def calendar():
    return await cached("calendar", "calendar", 900, data.economic_calendar)


@router.get("/calendar/earnings")
async def earnings(instrument: Instrument):
    return await cached("earnings", "yahoo", 900, data.earnings, instrument)
