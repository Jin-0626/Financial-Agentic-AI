"""
Yahoo Finance Direct JSON API Client
Fetches market summary, trending symbols, day movers, and options chains without yfinance.
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any, Dict, List, Optional
import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

# Primary and fallback base hosts
PRIMARY_BASE_URL = "https://query1.finance.yahoo.com"
FALLBACK_BASE_URL = "https://query2.finance.yahoo.com"

# Standard browser headers to avoid instant 403 blocks from Yahoo
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://finance.yahoo.com",
    "Referer": "https://finance.yahoo.com/",
}

_session = requests.Session()
_adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=2)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)


def _request_yahoo(endpoint: str, params: Optional[Dict[str, Any]] = None, timeout: float = 10.0) -> Any:
    """Execute GET request with dual-endpoint fallback and structured error handling."""
    params = params or {}
    clean_endpoint = endpoint.lstrip("/")

    for base_url in [PRIMARY_BASE_URL, FALLBACK_BASE_URL]:
        url = f"{base_url}/{clean_endpoint}"
        try:
            resp = _session.get(url, params=params, headers=DEFAULT_HEADERS, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code in (401, 403):
                continue  # Retry on the secondary query endpoint
            else:
                return {"error": f"HTTP {resp.status_code}", "detail": resp.text[:200]}
        except requests.RequestException as e:
            continue

    return {"error": f"Failed to fetch data from {clean_endpoint} after checking hosts"}


# -----------------------------------------------------------------------------
# Clean API Endpoints
# -----------------------------------------------------------------------------

def get_market_summary(region: str = "US") -> List[Dict[str, Any]]:
    """
    Fetch market indexes overview (S&P 500, Nasdaq, Dow, Crude, Gold, BTC, etc.).
    Returns a cleaned list of quotes.
    """
    data = _request_yahoo("v6/finance/quote/marketSummary", {"region": region, "lang": "en-US"})
    if "error" in data:
        return [data]

    market_items = data.get("marketSummaryResponse", {}).get("result", [])
    cleaned = []
    for item in market_items:
        cleaned.append({
            "symbol": item.get("symbol"),
            "short_name": item.get("shortName"),
            "market_state": item.get("marketState"),
            "price": item.get("regularMarketPrice", {}).get("raw"),
            "change": item.get("regularMarketChange", {}).get("raw"),
            "change_percent": item.get("regularMarketChangePercent", {}).get("raw"),
            "exchange": item.get("exchange"),
        })
    return cleaned


def get_trending(region: str = "US", count: int = 15) -> List[Dict[str, Any]]:
    """Fetch symbols currently trending in a specific market region."""
    data = _request_yahoo(f"v1/finance/trending/{region}", {"count": count})
    if "error" in data:
        return [data]

    results = data.get("finance", {}).get("result", [])
    if not results:
        return []

    quotes = results[0].get("quotes", [])
    return [{"symbol": q.get("symbol")} for q in quotes if "symbol" in q]


def get_movers(category: str = "gainers", count: int = 25, region: str = "US") -> List[Dict[str, Any]]:
    """
    Fetch market movers using Yahoo screeners.
    Categories: 'gainers', 'losers', 'active'
    """
    screener_map = {
        "gainers": "day_gainers",
        "losers": "day_losers",
        "active": "most_actives",
        "most_actives": "most_actives"
    }
    scr_id = screener_map.get(category.lower(), "day_gainers")
    data = _request_yahoo(
        "v1/finance/screener/predefined/saved",
        {"scrIds": scr_id, "count": count, "region": region}
    )
    if "error" in data:
        return [data]

    res = data.get("finance", {}).get("result", [])
    if not res:
        return []

    quotes = res[0].get("quotes", [])
    cleaned = []
    for q in quotes:
        cleaned.append({
            "symbol": q.get("symbol"),
            "name": q.get("shortName") or q.get("longName"),
            "price": q.get("regularMarketPrice"),
            "change": q.get("regularMarketChange"),
            "change_percent": q.get("regularMarketChangePercent"),
            "volume": q.get("regularMarketVolume"),
            "market_cap": q.get("marketCap"),
        })
    return cleaned


def get_options_chain(symbol: str, expiration_timestamp: Optional[int] = None) -> Dict[str, Any]:
    """
    Fetch options chain for a given ticker.
    Optional expiration timestamp (epoch seconds).
    """
    params = {}
    if expiration_timestamp:
        params["date"] = int(expiration_timestamp)

    data = _request_yahoo(f"v7/finance/options/{symbol.upper()}", params)
    if "error" in data:
        return data

    res = data.get("optionChain", {}).get("result", [])
    if not res:
        return {"error": "No option data found", "symbol": symbol}

    payload = res[0]
    options_bundle = payload.get("options", [{}])[0]

    return {
        "symbol": symbol.upper(),
        "underlying_price": payload.get("quote", {}).get("regularMarketPrice"),
        "available_expiration_dates": payload.get("expirationDates", []),
        "calls": options_bundle.get("calls", []),
        "puts": options_bundle.get("puts", []),
    }


def get_recommendations(symbol: str) -> List[Dict[str, Any]]:
    """Fetch peer recommendations / similar symbols."""
    data = _request_yahoo(f"v6/finance/recommendationsbysymbol/{symbol.upper()}", {"lang": "en-US"})
    if "error" in data:
        return [data]

    results = data.get("finance", {}).get("result", [])
    if not results:
        return []

    recommended = results[0].get("recommendedSymbols", [])
    return [{"symbol": item.get("symbol"), "score": item.get("score")} for item in recommended]


# -----------------------------------------------------------------------------
# CLI Entry Point
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Yahoo Finance Fast Market Data Fetcher")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # summary
    p_sum = subparsers.add_parser("summary", help="Major market index summary")
    p_sum.add_argument("--region", default="US")

    # trending
    p_trend = subparsers.add_parser("trending", help="Trending tickers")
    p_trend.add_argument("--region", default="US")
    p_trend.add_argument("--count", type=int, default=15)

    # movers
    p_mov = subparsers.add_parser("movers", help="Gainers, losers, or most active")
    p_mov.add_argument("category", choices=["gainers", "losers", "active"], default="gainers")
    p_mov.add_argument("--region", default="US")
    p_mov.add_argument("--count", type=int, default=20)

    # options
    p_opt = subparsers.add_parser("options", help="Options chain for a symbol")
    p_opt.add_argument("symbol")
    p_opt.add_argument("--date", type=int, default=None, help="Expiration date epoch timestamp")

    # recommendations
    p_rec = subparsers.add_parser("similar", help="Similar/Peer symbols")
    p_rec.add_argument("symbol")

    args = parser.parse_args()

    out = None
    if args.command == "summary":
        out = get_market_summary(region=args.region)
    elif args.command == "trending":
        out = get_trending(region=args.region, count=args.count)
    elif args.command == "movers":
        out = get_movers(category=args.category, count=args.count, region=args.region)
    elif args.command == "options":
        out = get_options_chain(symbol=args.symbol, expiration_timestamp=args.date)
    elif args.command == "similar":
        out = get_recommendations(symbol=args.symbol)

    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()