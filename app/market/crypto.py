import logging
from app.market import common
from app.market import transport

logger = logging.getLogger(__name__)


def crypto():
    rows = transport.cg(
        "coins/markets",
        {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 30,
            "page": 1,
            "sparkline": "true",
        },
    )
    global_data = transport.cg("global").get("data", {})
    return common.result(
        [
            {
                "instrument": common.instrument("coingecko:" + row["id"], row),
                "price": common.decimal(row.get("current_price")),
                "market_cap": common.number(row.get("market_cap")),
                "change_percent": common.number(row.get("price_change_percentage_24h")),
                "sparkline": ((row.get("sparkline_in_7d") or {}).get("price") or [])[-168:],
                "observed_at": row.get("last_updated"),
            }
            for row in rows
        ],
        "CoinGecko",
        "Top 30 assets by provider market cap",
        currency="USD",
        dominance={
            key: global_data.get("market_cap_percentage", {}).get(key) for key in ("btc", "eth")
        },
    )
