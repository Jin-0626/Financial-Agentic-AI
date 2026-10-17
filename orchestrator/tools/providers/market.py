from __future__ import annotations
import logging
from typing import Literal
from langchain_core.tools import BaseTool, tool
from app.infrastructure.diagnostics import sanitize_error
from . import common

logger = logging.getLogger(__name__)


def _make_market_data_tool() -> BaseTool:
    @tool
    def market_data(
        symbol: str,
        data_type: str = "quote",
        period: str = "1mo",
        interval: str = "1d",
        frequency: Literal["latest", "annual", "quarterly"] = "latest",
    ) -> str:
        """
        Fetch real-time quotes, historical OHLCV bars, company ratios, or top market movers.

        Args:
            symbol: Ticker symbol (e.g. 'AAPL', 'NVDA', 'EURUSD=X', 'BTC-USD').
                    Use 'MOVERS_GAINERS' or 'MOVERS_LOSERS' to scan broad market movers.
            data_type: 'quote' (real-time price snapshot),
                       'history' (historical OHLCV candle series),
                       'overview' (valuation multiples, P/E, beta, market cap),
                       'financials' (latest available quarterly/annual income statement, balance sheet and cash flow).
            frequency: latest (default), annual or quarterly. Never mix quarterly and annual totals.
            period: Date window for historical data ('1mo', '3mo', '6mo', '1y', '5y'). Default: '1mo'.
            interval: Bar frequency ('1d', '1wk', '1h'). Default: '1d'.

        Returns:
            JSON string with market data.
        """
        sym = symbol.strip().upper()
        data_type = data_type.strip().lower()
        if not sym or len(sym) > 64:
            return common._to_json(
                {
                    "error": "Supply a ticker symbol; use company_search to resolve a company name",
                    "category": "validation",
                }
            )

        try:
            if sym in ("MOVERS_GAINERS", "GAINERS", "MOVERS_LOSERS", "LOSERS"):
                if common.yahoo_market_data is None:
                    return common._to_json(
                        {
                            "error": "Yahoo dependency unavailable",
                            "category": "dependency",
                            "detail": common._dependency_errors.get("yahoo"),
                        }
                    )
                category = "gainers" if sym in ("MOVERS_GAINERS", "GAINERS") else "losers"
                return common._to_json(common.yahoo_market_data.get_movers(category, count=15))
            if common.stock_data is None:
                return common._to_json(
                    {
                        "error": "Market data dependency unavailable",
                        "category": "dependency",
                        "detail": common._dependency_errors.get("yfinance", "yf_data not loaded"),
                    }
                )
            if data_type == "quote":
                return common._to_json(common.stock_data.get_quote(sym))
            elif data_type == "history":
                return common._to_json(
                    common.stock_data.get_historical_period(sym, period=period, interval=interval)
                )
            elif data_type == "overview":
                return common._to_json(common.stock_data.get_info(sym))
            elif data_type == "financials":
                return common._to_json(common.stock_data.get_financials(sym, frequency=frequency))
            else:
                return common._to_json(
                    {
                        "error": f"Unknown data_type: {data_type}. Use quote, history, overview, or financials."
                    }
                )
        except Exception as e:
            return common._to_json(
                {"error": f"Failed fetching market data for {sym}: {sanitize_error(e)}"}
            )

    return market_data


def _make_company_search_tool() -> BaseTool:
    @tool
    def company_search(query: str, count: int = 5) -> str:
        """Resolve a company name or ticker to provider-listed symbols before market_data.

        query is a company name or ticker; count is 1 to 10. Results include the
        provider's symbol, company name, exchange and security type. Do not infer a
        company identity from a ticker or silently choose an ambiguous match.
        """
        if not query.strip() or len(query) > 200 or not 1 <= count <= 10:
            return common._to_json(
                {
                    "status": "error",
                    "category": "validation",
                    "error": "Supply a query of 1 to 200 characters and count from 1 to 10",
                }
            )
        try:
            from app.infrastructure.providers import search_symbols

            matches = search_symbols(query.strip(), count)
            return common._to_json(
                {
                    "status": "success" if matches else "empty",
                    "matches": matches,
                    "provider": "Yahoo Finance",
                }
            )
        except Exception as error:
            return common._to_json(
                {
                    "status": "error",
                    "category": "provider",
                    "error": sanitize_error(error),
                    "matches": [],
                }
            )

    return company_search
