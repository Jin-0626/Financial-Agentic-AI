from app.market import common
from app.market import transport


def snapshot(market):
    """Expose complete provider data without applying a native client's filters."""
    return screener(market, None, None, None, None, "symbol", False, 0, 20000)


def screener(market, sector, min_cap, min_change, min_volume, sort, descending, offset, limit):
    if market == "MY":
        from app.market.malaysia import screener as malaysia_screener

        return malaysia_screener(
            sector, min_cap, min_change, min_volume, sort, descending, offset, limit
        )
    if market != "US":
        raise ValueError("Unsupported screener market")
    from app.market.us import screener as us_screener

    return us_screener(sector, min_cap, min_change, min_volume, sort, descending, offset, limit)


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
