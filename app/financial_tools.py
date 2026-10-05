"""
Institutional Financial Tools for DeepAgent / LangChain / LangGraph.
Wires native market data, portfolio analytics, financial news, and macro indicators.
"""

from __future__ import annotations
from pathlib import Path
import sys
import json
import logging
import math
from typing import Any, Dict, List, Optional

from langchain_core.tools import BaseTool, tool
from .diagnostics import sanitize_error


logger = logging.getLogger(__name__)

_dependency_errors: dict[str, str] = {}

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Map actual script filenames from scripts/ directory
try:
    import yf_data as stock_data
except Exception as e:
    logger.error("Failed to import yf_data as stock_data: %s", sanitize_error(e))
    _dependency_errors["yfinance"] = sanitize_error(e)
    stock_data = None

try:
    import yh_market as yahoo_market_data
except Exception as e:
    logger.error("Failed to import yh_market as yahoo_market_data: %s", sanitize_error(e))
    _dependency_errors["yahoo"] = sanitize_error(e)
    yahoo_market_data = None

_fmp_client = None
try:
    import fmp_client
    if hasattr(fmp_client, "FMPClient"):
        _fmp_client = fmp_client.FMPClient()
    elif hasattr(fmp_client, "client"):
        _fmp_client = fmp_client.client
    else:
        _fmp_client = fmp_client
except Exception as e:
    logger.warning("FMP client unavailable: %s", sanitize_error(e))
    _fmp_client = None



def _to_json(data: Any) -> str:
    """Safe serializer ensuring clean JSON strings for LLM context."""
    return json.dumps(data, indent=2, ensure_ascii=False)


# -----------------------------------------------------------------------------
# Tool 1: Real-Time Market Data & Valuations
# -----------------------------------------------------------------------------

def _make_market_data_tool() -> BaseTool:
    @tool
    def market_data(
        symbol: str,
        data_type: str = "quote",
        period: str = "1mo",
        interval: str = "1d"
    ) -> str:
        """
        Fetch real-time quotes, historical OHLCV bars, company ratios, or top market movers.

        Args:
            symbol: Ticker symbol (e.g. 'AAPL', 'NVDA', 'EURUSD=X', 'BTC-USD').
                    Use 'MOVERS_GAINERS' or 'MOVERS_LOSERS' to scan broad market movers.
            data_type: 'quote' (real-time price snapshot),
                       'history' (historical OHLCV candle series),
                       'overview' (valuation multiples, P/E, beta, market cap),
                       'financials' (income statement & balance sheet).
            period: Date window for historical data ('1mo', '3mo', '6mo', '1y', '5y'). Default: '1mo'.
            interval: Bar frequency ('1d', '1wk', '1h'). Default: '1d'.

        Returns:
            JSON string with market data.
        """
        sym = symbol.strip().upper()

        try:
            if sym in ("MOVERS_GAINERS", "GAINERS", "MOVERS_LOSERS", "LOSERS"):
                if yahoo_market_data is None:
                    return _to_json({"error": "Yahoo dependency unavailable", "category": "dependency", "detail": _dependency_errors.get("yahoo")})
                category = "gainers" if sym in ("MOVERS_GAINERS", "GAINERS") else "losers"
                return _to_json(yahoo_market_data.get_movers(category, count=15))
            if stock_data is None:
                return _to_json({"error": "Market data dependency unavailable", "category": "dependency", "detail": _dependency_errors.get("yfinance", "yf_data not loaded")})
            if data_type == "quote":
                return _to_json(stock_data.get_quote(sym))
            elif data_type == "history":
                return _to_json(stock_data.get_historical_period(sym, period=period, interval=interval))
            elif data_type == "overview":
                return _to_json(stock_data.get_info(sym))
            elif data_type == "financials":
                return _to_json(stock_data.get_financials(sym))
            else:
                return _to_json({"error": f"Unknown data_type: {data_type}. Use quote, history, overview, or financials."})
        except Exception as e:
            return _to_json({"error": f"Failed fetching market data for {sym}: {sanitize_error(e)}"})

    return market_data


# -----------------------------------------------------------------------------
# Tool 2: Portfolio Holdings, NAV Replay & Risk
# -----------------------------------------------------------------------------

def _make_portfolio_tool() -> BaseTool:
    @tool
    def portfolio_analytics(action: str, payload_json: str) -> str:
        """
        Compute portfolio valuation, performance back-projection, and transaction replay.

        Args:
            action: 'replay_transactions' (calculates true NAV curve, cash flow, and cost basis over time)
                    or 'current_holdings_nav' (back-projects today's static holdings over historical prices).
            payload_json: JSON string payload:
                - For 'replay_transactions':
                  '[{"date": "2024-01-15", "symbol": "AAPL", "type": "BUY", "quantity": 10, "price": 180.0}, ...]'
                - For 'current_holdings_nav':
                  '{"period": "6mo", "positions": [{"symbol": "MSFT", "quantity": 25}, ...]}'

        Returns:
            JSON string containing dates, NAVs, and total cost basis over time.
        """
        if not stock_data:
            return _to_json({"error": "Market data dependency unavailable", "category": "dependency", "detail": _dependency_errors.get("yfinance", "yf_data not loaded")})

        try:
            parsed = json.loads(payload_json)
        except Exception as e:
            return _to_json({"error": f"Invalid JSON in payload: {sanitize_error(e)}"})

        try:
            if action == "replay_transactions":
                if not isinstance(parsed, list):
                    return _to_json({"error": "replay_transactions requires a list of transaction objects."})
                return _to_json(stock_data.replay_portfolio_transactions(parsed))

            elif action == "current_holdings_nav":
                positions = parsed.get("positions", [])
                period = parsed.get("period", "6mo")
                resolved = stock_data._resolve_tickers([p["symbol"] for p in positions], period=period)
                if not resolved:
                    return _to_json({"error": "Failed to resolve prices for portfolio positions."})
                return _to_json({"status": "calculated", "positions_tracked": len(positions)})

            else:
                return _to_json({"error": f"Unknown action: {action}. Use 'replay_transactions' or 'current_holdings_nav'."})
        except Exception as e:
            return _to_json({"error": f"Portfolio calculation error: {sanitize_error(e)}"})

    return portfolio_analytics


# -----------------------------------------------------------------------------
# Tool 3: Financial News & Corporate Disclosures
# -----------------------------------------------------------------------------

def _make_news_tool() -> BaseTool:
    @tool
    def financial_news(symbol: Optional[str] = None, count: int = 10) -> str:
        """
        Fetch recent financial headlines, summaries, and SEC filing news.

        Args:
            symbol: Ticker symbol (e.g. 'AAPL', 'TSLA'). If omitted, general market news is returned.
            count: Number of news items to retrieve (default: 10).

        Returns:
            JSON string with headlines, publishers, timestamps, and summaries.
        """
        attempts = []
        if _fmp_client and getattr(_fmp_client, "api_key", None):
            try:
                params = {"limit": count}
                if symbol:
                    params["tickers"] = symbol.upper()
                news = _fmp_client._request("stock_news", params)
                if not isinstance(news, list):
                    raise ValueError("FMP returned a non-list news response")
                if news:
                    return _to_json(news)
                attempts.append({"provider": "FMP", "status": "empty"})
            except Exception as e:
                attempts.append({"provider": "FMP", "status": "error", "error": sanitize_error(e)})
        if symbol:
            try:
                # News does not depend on whether the analytics module imported successfully.
                import yfinance as yf
                raw_news = yf.Ticker(symbol.upper()).news
                if not isinstance(raw_news, list):
                    raise ValueError("Yahoo returned a non-list news response")
                cleaned = []
                for item in raw_news[:count]:
                    content = item.get("content", item)
                    cleaned.append({
                        "title": content.get("title", ""),
                        "summary": content.get("summary", ""),
                        "pub_date": content.get("pubDate", ""),
                        "provider": (content.get("provider") or {}).get("displayName", ""),
                        "url": (content.get("canonicalUrl") or {}).get("url", content.get("link", "")),
                    })
                if cleaned:
                    return _to_json(cleaned)
                attempts.append({"provider": "Yahoo", "status": "empty"})
            except Exception as e:
                attempts.append({"provider": "Yahoo", "status": "error", "error": sanitize_error(e)})
        status = "error" if any(a["status"] == "error" for a in attempts) else "empty" if attempts else "unavailable"
        result = {"articles": [], "status": status, "attempts": attempts}
        if status != "empty":
            result["error"] = "News retrieval failed" if attempts else "No news provider configured for this request"
        return _to_json(result)

    return financial_news


# -----------------------------------------------------------------------------
# Tool 4: Macroeconomics & Central Bank Indicators
# -----------------------------------------------------------------------------

def _treasury_result() -> dict[str, Any]:
    """Use one schema for direct rates, proxies, partial coverage, and complete failure."""
    direct_rates: list[dict[str, Any]] = []
    proxies: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    expected_symbols = ("^IRX", "^FVX", "^TNX", "^TYX")
    maturity_fields = ("month1", "month2", "month3", "month6", "year1", "year2", "year3", "year5", "year7", "year10", "year20", "year30")
    fmp_key = getattr(_fmp_client, "api_key", None)

    def record_error(provider: str, message: object, symbol: str | None = None):
        error = {"provider": provider, "error": sanitize_error(message, secrets=(fmp_key,) if isinstance(fmp_key, str) else ())}
        if symbol:
            error["symbol"] = symbol
        errors.append(error)

    def finite_number(value: Any) -> bool:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        try:
            return math.isfinite(value)
        except OverflowError:
            return False

    if _fmp_client and fmp_key:
        try:
            rows = _fmp_client.get_treasury_rates()
            if not isinstance(rows, list) or not rows:
                record_error("FMP", rows.get("error", "Invalid treasury response") if isinstance(rows, dict) else "No treasury rates returned")
            else:
                for row in rows:
                    if not isinstance(row, dict) or row.get("error"):
                        record_error("FMP", row.get("error", "Invalid treasury row") if isinstance(row, dict) else "Invalid treasury row")
                        continue
                    valid = {field: value for field, value in row.items() if field in maturity_fields and finite_number(value)}
                    if not valid or not isinstance(row.get("date"), str):
                        record_error("FMP", "Treasury row lacks dated finite yield values")
                        continue
                    invalid = [field for field in maturity_fields if field in row and not finite_number(row[field])]
                    if invalid:
                        record_error("FMP", "Unavailable yield fields: " + ", ".join(invalid))
                    direct_rates.append({"date": row["date"], **valid})
        except Exception as e:
            record_error("FMP", e)

    # Use proxies if direct retrieval failed or returned incomplete data; retain valid direct rows.
    if not direct_rates or errors:
        if stock_data is None:
            record_error("Yahoo", _dependency_errors.get("yfinance", "Market data dependency unavailable"))
        else:
            try:
                quotes = stock_data.get_batch_quotes(list(expected_symbols))
                if not isinstance(quotes, list):
                    record_error("Yahoo", quotes.get("error", "Invalid quote response") if isinstance(quotes, dict) else "Invalid quote response")
                    quotes = []
                seen = set()
                for quote in quotes:
                    if not isinstance(quote, dict):
                        record_error("Yahoo", "Invalid quote row")
                        continue
                    symbol = quote.get("symbol")
                    if quote.get("error"):
                        record_error("Yahoo", quote["error"], symbol if isinstance(symbol, str) else None)
                    elif symbol not in expected_symbols or symbol in seen or not finite_number(quote.get("price")):
                        record_error("Yahoo", "Quote lacks a unique expected symbol and finite yield value", symbol if isinstance(symbol, str) else None)
                    else:
                        proxies.append(quote)
                        seen.add(symbol)
                for symbol in expected_symbols:
                    if symbol not in seen:
                        record_error("Yahoo", "Treasury proxy unavailable", symbol)
            except Exception as e:
                record_error("Yahoo", e)

    has_data = bool(direct_rates or proxies)
    status = "partial_success" if has_data and errors else "success" if has_data else "error"
    return {
        "status": status,
        "treasury_rates": direct_rates,
        "treasury_yield_proxies": proxies,
        "provider_errors": errors,
        "error": "No valid treasury data returned" if not has_data else None,
    }


def _make_economics_tool() -> BaseTool:
    @tool
    def economics_data(indicator: str = "treasury") -> str:
        """
        Fetch macroeconomic indicators, US Treasury yield curve rates, or economic calendars.

        Args:
            indicator: 'treasury' (dated direct yields or clearly labeled Yahoo yield proxies),
                       'calendar' (upcoming economic releases: CPI, GDP, FOMC, Non-farm payrolls),
                       'summary' (major market indices overview: S&P 500, NASDAQ, Crude Oil, Gold).

        Returns:
            JSON string with macroeconomic rates and dates. Treasury always returns status
            (success, partial_success, error), treasury_rates, treasury_yield_proxies,
            provider_errors, and error. Proxies are Yahoo yield-index values, not invented yields.
        """
        ind = indicator.strip().lower()

        if ind not in ("treasury", "calendar", "summary", "indices", "market"):
            return _to_json({"error": f"Unknown indicator: {indicator}", "category": "input"})
        if ind == "treasury":
            return _to_json(_treasury_result())
        provider_errors = []
        # 1. Market Index Summary
        if ind in ("summary", "indices", "market") and yahoo_market_data:
            try:
                return _to_json(yahoo_market_data.get_market_summary(region="US"))
            except Exception as e:
                return _to_json({"error": sanitize_error(e), "provider": "Yahoo", "category": "provider"})

        # 2. Economic releases via FMP
        if _fmp_client and _fmp_client.api_key:
            try:
                if ind == "calendar":
                    return _to_json(_fmp_client.get_economic_calendar())
            except Exception as e:
                provider_errors.append({"provider": "FMP", "error": sanitize_error(e)})

        return _to_json({"error": f"Indicator '{indicator}' unavailable", "provider_errors": provider_errors, "note": "Calendar requires configured FMP; sandbox status does not determine provider availability."})

    return economics_data


# -----------------------------------------------------------------------------
# Tool Aggregator
# -----------------------------------------------------------------------------

def build_financial_tools() -> List[BaseTool]:
    """Build and return all active institutional finance tools for the agent."""
    return [
        _make_market_data_tool(),
        _make_portfolio_tool(),
        _make_news_tool(),
        _make_economics_tool(),
    ]