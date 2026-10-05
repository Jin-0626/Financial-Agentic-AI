"""
Financial Modeling Prep (FMP) Data Client
Modular, production-grade client for fundamental statements, market intelligence, and ratios.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Union
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

logger = logging.getLogger(__name__)


class FMPError(Exception):
    """Base exception for FMP API interactions."""
    def __init__(self, message: str, status_code: Optional[int] = None, payload: Optional[Any] = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class FMPAuthError(FMPError):
    """Raised on invalid key, quota exhaustion, or premium endpoint restrictions."""
    pass


class FMPRateLimitError(FMPError):
    """Raised when HTTP 429 Too Many Requests is returned."""
    pass


class FMPClient:
    """Client for querying Financial Modeling Prep API endpoints."""

    BASE_URL_V3 = "https://financialmodelingprep.com/api/v3"
    BASE_URL_V4 = "https://financialmodelingprep.com/api/v4"

    def __init__(self, api_key: Optional[str] = None, timeout: float = 20.0):
        self.api_key = api_key or os.environ.get("FMP_API_KEY", "")
        if not self.api_key:
            logger.warning("FMP_API_KEY is not configured. Requests may fail with 401 Unauthorized.")

        self.timeout = timeout
        self.session = requests.Session()

        # Mount resilient retry strategy for transient errors & throttling
        retries = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            raise_on_status=False
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=20)
        self.session.mount("https://", adapter)
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; ModernFMPClient/1.0)",
            "Accept": "application/json"
        })

    def _request(self, endpoint: str, params: Optional[Dict[str, Any]] = None, version: str = "v3") -> Any:
        """Internal dispatcher executing authenticated HTTP requests."""
        base = self.BASE_URL_V4 if version == "v4" else self.BASE_URL_V3
        url = f"{base}/{endpoint.lstrip('/')}"

        query_params = {"apikey": self.api_key}
        if params:
            # Filter out None values
            query_params.update({k: v for k, v in params.items() if v is not None})

        try:
            resp = self.session.get(url, params=query_params, timeout=self.timeout)
            
            if resp.status_code in (401, 403):
                raise FMPAuthError(f"FMP authentication failed ({resp.status_code}): {resp.text}", status_code=resp.status_code)
            elif resp.status_code == 429:
                raise FMPRateLimitError("FMP rate limit exceeded (HTTP 429).", status_code=429)
            elif resp.status_code >= 400:
                raise FMPError(f"FMP HTTP Error {resp.status_code}: {resp.text}", status_code=resp.status_code)

            data = resp.json()

            # Handle FMP API inline error envelopes
            if isinstance(data, dict):
                err = data.get("Error Message") or data.get("error")
                if err:
                    lowered = str(err).lower()
                    if any(k in lowered for k in ["upgrade", "subscription", "restricted", "unauthorized"]):
                        raise FMPAuthError(f"Subscription restriction: {err}", payload=data)
                    raise FMPError(f"FMP returned error: {err}", payload=data)

            return data

        except requests.RequestException as e:
            raise FMPError(f"Network error communicating with FMP: {str(e)}") from e

    # -------------------------------------------------------------------------
    # Core Fundamentals & Statements
    # -------------------------------------------------------------------------

    def get_company_profile(self, symbol: str) -> Dict[str, Any]:
        """Fetch general corporate profile, CEO, sector, description, and currency."""
        data = self._request(f"profile/{symbol.upper()}")
        if isinstance(data, list) and data:
            return data[0]
        return data if isinstance(data, dict) else {}

    def get_income_statement(self, symbol: str, period: str = "annual", limit: int = 5) -> List[Dict[str, Any]]:
        """Fetch historical income statements (period: 'annual' or 'quarter')."""
        return self._request(f"income-statement/{symbol.upper()}", {"period": period, "limit": limit})

    def get_balance_sheet(self, symbol: str, period: str = "annual", limit: int = 5) -> List[Dict[str, Any]]:
        """Fetch historical balance sheet statements (period: 'annual' or 'quarter')."""
        return self._request(f"balance-sheet-statement/{symbol.upper()}", {"period": period, "limit": limit})

    def get_cash_flow(self, symbol: str, period: str = "annual", limit: int = 5) -> List[Dict[str, Any]]:
        """Fetch historical cash flow statements (period: 'annual' or 'quarter')."""
        return self._request(f"cash-flow-statement/{symbol.upper()}", {"period": period, "limit": limit})

    def get_financial_ratios(self, symbol: str, period: str = "annual", limit: int = 5) -> List[Dict[str, Any]]:
        """Fetch valuation and performance ratios (period: 'annual', 'quarter', or 'ttm')."""
        sym = symbol.upper()
        if period.lower() == "ttm":
            data = self._request(f"ratios-ttm/{sym}")
            return [data] if isinstance(data, dict) else data
        return self._request(f"ratios/{sym}", {"period": period, "limit": limit})

    def get_key_metrics(self, symbol: str, period: str = "annual", limit: int = 5) -> List[Dict[str, Any]]:
        """Fetch key metrics (EV/EBITDA, Debt-to-Equity, FCF Yield, ROIC)."""
        return self._request(f"key-metrics/{symbol.upper()}", {"period": period, "limit": limit})

    # -------------------------------------------------------------------------
    # Pricing & Quotes
    # -------------------------------------------------------------------------

    def get_quote(self, symbols: Union[str, List[str]]) -> List[Dict[str, Any]]:
        """Fetch real-time quotes for one or multiple comma-separated symbols."""
        if isinstance(symbols, list):
            sym_str = ",".join(s.strip().upper() for s in symbols)
        else:
            sym_str = symbols.strip().upper()
        data = self._request(f"quote/{sym_str}")
        return data if isinstance(data, list) else [data]

    def get_historical_prices(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        interval: str = "1d"
    ) -> List[Dict[str, Any]]:
        """
        Fetch historical candles.
        interval: '1m', '5m', '15m', '30m', '1h', '4h', or '1d'.
        """
        sym = symbol.upper()
        interval_map = {
            "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
            "1h": "1hour", "4h": "4hour", "1d": "day"
        }
        fmp_interval = interval_map.get(interval, "day")

        # Fallback date defaults (1 year range)
        if not end_date:
            end_date = datetime.now().strftime("%Y-%m-%d")
        if not start_date:
            start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

        params = {"from": start_date, "to": end_date}

        if fmp_interval == "day":
            data = self._request(f"historical-price-full/{sym}", params)
            return data.get("historical", []) if isinstance(data, dict) else []
        else:
            data = self._request(f"historical-chart/{fmp_interval}/{sym}", params)
            return data if isinstance(data, list) else []

    # -------------------------------------------------------------------------
    # Macro & Institutional Data
    # -------------------------------------------------------------------------

    def get_treasury_rates(self, from_date: Optional[str] = None, to_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch US Treasury yields across maturities."""
        params = {"from": from_date, "to": to_date}
        data = self._request("treasury", params, version="v4")
        return data if isinstance(data, list) else []

    def get_economic_calendar(self, from_date: Optional[str] = None, to_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch economic indicator releases (CPI, Non-Farm Payrolls, FOMC)."""
        params = {"from": from_date, "to": to_date}
        data = self._request("economic_calendar", params)
        return data if isinstance(data, list) else []

    def get_insider_trades(self, symbol: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch corporate insider transactions filed via SEC Form 4."""
        return self._request(f"insider-trading/{symbol.upper()}", {"limit": limit}, version="v4")

    def get_institutional_holders(self, symbol: str) -> List[Dict[str, Any]]:
        """Fetch 13F institutional fund ownership positions."""
        return self._request(f"institutional-holder/{symbol.upper()}")

    # -------------------------------------------------------------------------
    # Comprehensive Company Overview
    # -------------------------------------------------------------------------

    def get_full_overview(self, symbol: str) -> Dict[str, Any]:
        """Aggregate profile, real-time quote, TTM ratios, and key metrics in one payload."""
        sym = symbol.upper()
        out = {"symbol": sym}

        try:
            out["profile"] = self.get_company_profile(sym)
        except Exception as e:
            out["profile"] = {"error": str(e)}

        try:
            quotes = self.get_quote(sym)
            out["quote"] = quotes[0] if quotes else {}
        except Exception as e:
            out["quote"] = {"error": str(e)}

        try:
            ratios = self.get_financial_ratios(sym, period="ttm")
            out["ratios_ttm"] = ratios[0] if ratios else {}
        except Exception as e:
            out["ratios_ttm"] = {"error": str(e)}

        try:
            metrics = self.get_key_metrics(sym, period="annual", limit=1)
            out["key_metrics"] = metrics[0] if metrics else {}
        except Exception as e:
            out["key_metrics"] = {"error": str(e)}

        return out


# -----------------------------------------------------------------------------
# CLI Entry Point
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Financial Modeling Prep (FMP) CLI Tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # quote
    p_q = subparsers.add_parser("quote", help="Get quote for symbol(s)")
    p_q.add_argument("symbols", help="One or more symbols (e.g. AAPL or AAPL,MSFT)")

    # profile
    p_prof = subparsers.add_parser("profile", help="Get company profile")
    p_prof.add_argument("symbol")

    # statement
    p_stmt = subparsers.add_parser("statement", help="Fetch financial statements")
    p_stmt.add_argument("symbol")
    p_stmt.add_argument("type", choices=["income", "balance", "cashflow"], default="income")
    p_stmt.add_argument("--period", choices=["annual", "quarter"], default="annual")
    p_stmt.add_argument("--limit", type=int, default=5)

    # ratios
    p_rat = subparsers.add_parser("ratios", help="Get financial ratios")
    p_rat.add_argument("symbol")
    p_rat.add_argument("--period", choices=["annual", "quarter", "ttm"], default="ttm")
    p_rat.add_argument("--limit", type=int, default=5)

    # history
    p_hist = subparsers.add_parser("history", help="Get historical prices")
    p_hist.add_argument("symbol")
    p_hist.add_argument("--from-date", dest="start_date", default=None)
    p_hist.add_argument("--to-date", dest="end_date", default=None)
    p_hist.add_argument("--interval", default="1d")

    # overview
    p_ov = subparsers.add_parser("overview", help="Get comprehensive overview")
    p_ov.add_argument("symbol")

    # macro
    subparsers.add_parser("treasury", help="Get US Treasury rates")
    subparsers.add_parser("calendar", help="Get Economic calendar")

    args = parser.parse_args()
    client = FMPClient()

    try:
        if args.command == "quote":
            result = client.get_quote(args.symbols)
        elif args.command == "profile":
            result = client.get_company_profile(args.symbol)
        elif args.command == "statement":
            if args.type == "income":
                result = client.get_income_statement(args.symbol, period=args.period, limit=args.limit)
            elif args.type == "balance":
                result = client.get_balance_sheet(args.symbol, period=args.period, limit=args.limit)
            elif args.type == "cashflow":
                result = client.get_cash_flow(args.symbol, period=args.period, limit=args.limit)
        elif args.command == "ratios":
            result = client.get_financial_ratios(args.symbol, period=args.period, limit=args.limit)
        elif args.command == "history":
            result = client.get_historical_prices(args.symbol, start_date=args.start_date, end_date=args.end_date, interval=args.interval)
        elif args.command == "overview":
            result = client.get_full_overview(args.symbol)
        elif args.command == "treasury":
            result = client.get_treasury_rates()
        elif args.command == "calendar":
            result = client.get_economic_calendar()
        else:
            result = {"error": "Invalid command"}

        print(json.dumps(result, indent=2))

    except FMPError as e:
        print(json.dumps({"error": str(e), "type": type(e).__name__}, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()