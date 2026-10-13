import logging
from app.market import common
from app.market import transport

logger = logging.getLogger(__name__)

@transport.yahoo_bound
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
    queries = [transport.yf.EquityQuery("eq", ["region", "my" if market == "MY" else "us"])]
    if sector:
        queries.append(transport.yf.EquityQuery("eq", ["sector", sector]))
    for field, value in (
        ("intradaymarketcap", min_cap),
        ("percentchange", min_change),
        ("dayvolume", min_volume),
    ):
        if value is not None:
            queries.append(transport.yf.EquityQuery("gte", [field, value]))
    query = queries[0] if len(queries) == 1 else transport.yf.EquityQuery("and", queries)
    raw = transport.yf.screen(
        query, offset=offset, size=limit, sortField=fields[sort], sortAsc=not descending
    )
    rows = []
    for item in raw.get("quotes", []):
        if not item.get("symbol"):
            continue
        rows.append(
            {
                "instrument": common.instrument(item["symbol"], item),
                "currency": item.get("currency"),
                "sector": item.get("sector") or "Unclassified",
                "market_cap": common.number(item.get("marketCap")),
                "price": common.decimal(item.get("regularMarketPrice")),
                "volume": common.number(item.get("regularMarketVolume")),
                "change_percent": common.number(item.get("regularMarketChangePercent")),
            }
        )
    return common.result(
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
    raw = transport.fetch(
        "https://api.nasdaq.com/api/screener/stocks",
        {"tableonly": "true", "limit": 10000, "offset": 0},
        {"User-Agent": "Financial-Deep-Agents/1.0", "Accept": "application/json"},
    )
    rows = ((raw.get("data") or {}).get("table") or {}).get("rows", [])
    return common.result(
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

