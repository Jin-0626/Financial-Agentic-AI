import logging
from app.market import common
from app.market import transport

logger = logging.getLogger(__name__)

@transport.yahoo_bound
def search(query):
    found, failures = [], []
    try:
        for item in transport.yf.Search(
            query, max_results=20, news_count=0, lists_count=0, timeout=10
        ).quotes:
            if isinstance(item, dict) and item.get("symbol"):
                found.append(common.instrument(item["symbol"], item))
    except Exception:  # noqa: BLE001 -- isolate third-party provider failures; expose partial coverage
        failures.append("Yahoo symbol search unavailable")
    try:
        for coin in transport.cg("search", {"query": query}).get("coins", [])[:10]:
            found.append(common.instrument("coingecko:" + coin["id"], coin))
    except Exception:  # noqa: BLE001 -- isolate third-party provider failures; expose partial coverage
        failures.append("Crypto search unavailable")
    if not found and len(failures) == 2:
        raise RuntimeError("Symbol providers unavailable")
    return common.result(
        list({item["id"]: item for item in found}.values())[:25],
        "Yahoo Finance / CoinGecko",
        "Global provider search; exchange identity retained",
        limitations=failures,
    )

