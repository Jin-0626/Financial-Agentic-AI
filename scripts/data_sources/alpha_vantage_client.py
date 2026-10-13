"""
Alpha Vantage Financial Data Client
Clean wrapper supporting real-time quotes, historical candles, fundamentals, FX, and search.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

logger = logging.getLogger(__name__)


class AlphaVantageError(Exception):
    """Base exception for Alpha Vantage API issues."""
    pass


class AlphaVantageRateLimitError(AlphaVantageError):
    """Raised when Alpha Vantage standard call volume limits are reached."""
    pass


class AlphaVantageClient:
    """Client for Alpha Vantage REST APIs."""

    BASE_URL = "https://www.alphavantage.co/query"

    def __init__(self, api_key: Optional[str] = None, timeout: float = 15.0):
        self.api_key = api_key or os.environ.get("ALPHA_VANTAGE_API_KEY", "")
        if not self.api_key:
            logger.warning("ALPHA_VANTAGE_API_KEY is not set. Requests will fail.")
        self.timeout = timeout

        self.session = requests.Session()
        retries = Retry(total=2, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

    def _get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute request and handle Alpha Vantage 200-OK error and rate-limit envelopes."""
        query = {"apikey": self.api_key, **params}
        try:
            resp = self.session.get(self.BASE_URL, params=query, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()

            # Handle rate-limit warnings and notices
            if "Note" in data:
                raise AlphaVantageRateLimitError(f"API frequency limit reached: {data['Note']}")
            if "Information" in data and "rate limit" in data["Information"].lower():
                raise AlphaVantageRateLimitError(f"API notice: {data['Information']}")

            # Handle invalid query errors
            if "Error Message" in data:
                raise AlphaVantageError(f"Alpha Vantage Error: {data['Error Message']}")

            return data

        except requests.RequestException as e:
            raise AlphaVantageError(f"Network error communicating with Alpha Vantage: {str(e)}") from e

    # -------------------------------------------------------------------------
    # Endpoints
    # -------------------------------------------------------------------------

    def get_quote(self, symbol: str) -> Dict[str, Any]:
        """Fetch latest real-time stock quote and daily metrics."""
        data = self._get({"function": "GLOBAL_QUOTE", "symbol": symbol.strip().upper()})
        quote = data.get("Global Quote", {})

        if not quote:
            return {"error": f"No quote data returned for symbol: {symbol}"}

        def _clean_pct(val: str) -> float:
            return float(val.replace("%", "").strip()) if val else 0.0

        return {
            "symbol": quote.get("01. symbol", symbol.upper()),
            "price": float(quote.get("05. price", 0.0)),
            "change": float(quote.get("09. change", 0.0)),
            "change_percent": _clean_pct(quote.get("10. change percent", "0")),
            "volume": int(quote.get("06. volume", 0)),
            "open": float(quote.get("02. open", 0.0)),
            "high": float(quote.get("03. high", 0.0)),
            "low": float(quote.get("04. low", 0.0)),
            "previous_close": float(quote.get("08. previous close", 0.0)),
            "latest_trading_day": quote.get("07. latest trading day", ""),
        }

    def get_daily_history(self, symbol: str, outputsize: str = "compact") -> List[Dict[str, Any]]:
        """
        Fetch daily historical prices.
        outputsize: 'compact' (latest 100 data points) or 'full' (up to 20 years).
        """
        data = self._get({
            "function": "TIME_SERIES_DAILY_ADJUSTED",
            "symbol": symbol.strip().upper(),
            "outputsize": outputsize
        })

        series = data.get("Time Series (Daily)", {})
        if not series:
            return []

        out = []
        for date_str, metrics in sorted(series.items(), reverse=True):
            out.append({
                "date": date_str,
                "open": float(metrics.get("1. open", 0.0)),
                "high": float(metrics.get("2. high", 0.0)),
                "low": float(metrics.get("3. low", 0.0)),
                "close": float(metrics.get("4. close", 0.0)),
                "adjusted_close": float(metrics.get("5. adjusted close", 0.0)),
                "volume": int(metrics.get("6. volume", 0)),
                "dividend_amount": float(metrics.get("7. dividend amount", 0.0)),
            })
        return out

    def get_company_overview(self, symbol: str) -> Dict[str, Any]:
        """Fetch fundamental company information, ratios, balance-sheet stats, and margins."""
        data = self._get({"function": "OVERVIEW", "symbol": symbol.strip().upper()})
        if not data or not data.get("Symbol"):
            return {"error": f"No overview data available for symbol: {symbol}"}
        return data

    def get_exchange_rate(self, from_currency: str, to_currency: str = "USD") -> Dict[str, Any]:
        """Fetch real-time currency exchange rates (Forex / Crypto)."""
        data = self._get({
            "function": "CURRENCY_EXCHANGE_RATE",
            "from_currency": from_currency.strip().upper(),
            "to_currency": to_currency.strip().upper()
        })
        rate_data = data.get("Realtime Currency Exchange Rate", {})
        if not rate_data:
            return {"error": "Failed to retrieve exchange rate data."}

        return {
            "from_currency": rate_data.get("1. From_Currency Code"),
            "to_currency": rate_data.get("3. To_Currency Code"),
            "exchange_rate": float(rate_data.get("5. Exchange Rate", 0.0)),
            "bid_price": float(rate_data.get("8. Bid Price", 0.0)),
            "ask_price": float(rate_data.get("9. Ask Price", 0.0)),
            "last_refreshed": rate_data.get("6. Last Refreshed"),
            "timezone": rate_data.get("7. Time Zone"),
        }

    def search_symbol(self, keywords: str) -> List[Dict[str, Any]]:
        """Search tickers and company names."""
        data = self._get({"function": "SYMBOL_SEARCH", "keywords": keywords.strip()})
        matches = data.get("bestMatches", [])

        out = []
        for m in matches:
            out.append({
                "symbol": m.get("1. symbol"),
                "name": m.get("2. name"),
                "type": m.get("3. type"),
                "region": m.get("4. region"),
                "currency": m.get("8. currency"),
                "match_score": float(m.get("9. matchScore", 0.0)),
            })
        return out


# -----------------------------------------------------------------------------
# CLI Entry Point
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Alpha Vantage API Data Fetcher")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # quote
    p_quote = subparsers.add_parser("quote", help="Get real-time quote for a symbol")
    p_quote.add_argument("symbol", help="Stock ticker (e.g. MSFT, AAPL)")

    # history
    p_hist = subparsers.add_parser("history", help="Daily historical candles")
    p_hist.add_argument("symbol")
    p_hist.add_argument("--full", action="store_true", help="Download full history (default: compact 100 days)")

    # overview
    p_over = subparsers.add_parser("overview", help="Company fundamental overview")
    p_over.add_argument("symbol")

    # fx
    p_fx = subparsers.add_parser("fx", help="Foreign exchange or crypto rate")
    p_fx.add_argument("from_currency", help="Source currency (e.g. EUR, BTC)")
    p_fx.add_argument("--to", dest="to_currency", default="USD", help="Target currency (default: USD)")

    # search
    p_search = subparsers.add_parser("search", help="Search ticker symbols by keyword")
    p_search.add_argument("keyword")

    args = parser.parse_args()
    client = AlphaVantageClient()

    try:
        result = None
        if args.command == "quote":
            result = client.get_quote(args.symbol)
        elif args.command == "history":
            size = "full" if args.full else "compact"
            result = client.get_daily_history(args.symbol, outputsize=size)
        elif args.command == "overview":
            result = client.get_company_overview(args.symbol)
        elif args.command == "fx":
            result = client.get_exchange_rate(args.from_currency, args.to_currency)
        elif args.command == "search":
            result = client.search_symbol(args.keyword)

        print(json.dumps(result, indent=2))

    except (AlphaVantageError, AlphaVantageRateLimitError) as err:
        print(json.dumps({"error": str(err), "type": type(err).__name__}, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()