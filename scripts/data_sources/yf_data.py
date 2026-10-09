"""
Market Data & Portfolio Analytics Engine (yfinance)
Standalone module for CLI use, web APIs (FastAPI/Flask), or AI agent tool calling.
"""

from __future__ import annotations

import argparse
import os
import contextlib
import io
import json
import math
from datetime import date as _date, datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import yfinance as yf

# Configure network timeout (override with YF_NET_TIMEOUT env var)
try:
    _NET_TIMEOUT = float(os.environ.get("YF_NET_TIMEOUT", "15"))
except (TypeError, ValueError):
    _NET_TIMEOUT = 15.0

# -----------------------------------------------------------------------------
# Data Sanitization Helpers (Pure JSON compliance: converts NaN/Inf to None)
# -----------------------------------------------------------------------------


def _price_digits(value: float) -> int:
    """Calculates decimal precision dynamically based on asset magnitude."""
    try:
        a = abs(float(value))
    except (TypeError, ValueError):
        return 2
    if not math.isfinite(a) or a == 0 or a >= 1000:
        return 2
    if a >= 1:
        return 4
    return min(12, 4 - int(math.floor(math.log10(a))))


def _px(value: Any, digits: Optional[int] = None) -> Optional[float]:
    """Rounds float price safely, returning None on invalid/NaN values."""
    if value is None:
        return None
    try:
        v = float(value)
        if not math.isfinite(v):
            return None
        return round(v, _price_digits(v) if digits is None else digits)
    except (TypeError, ValueError):
        return None


def _int(value: Any, default: Optional[int] = None) -> Optional[int]:
    """Safely converts to int or returns default if NaN/invalid."""
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _sanitize(obj: Any) -> Any:
    """Recursively converts NaNs, infinities, and Timestamps to JSON-safe values."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple, set)):
        return [_sanitize(v) for v in obj]
    elif isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    elif isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    elif pd.isna(obj):
        return None
    return obj


# -----------------------------------------------------------------------------
# Core Market Fetchers
# -----------------------------------------------------------------------------


def get_quote(symbol: str) -> Dict[str, Any]:
    """Fetch real-time snapshot quote for a single symbol."""
    from app.providers import quote

    try:
        result = quote(symbol)
        return {
            **result,
            "price": float(result["price"]),
            "timestamp": result.get("as_of"),
        }
    except Exception as error:
        return {"error": str(error), "symbol": symbol}


def _history_records(
    hist: pd.DataFrame, symbol: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Serialize OHLCV bars consistently, retaining each public output shape."""
    out = []
    for index, row in hist.iterrows():
        close = _px(row["Close"])
        if close is None:
            continue
        record = {
            "timestamp": int(index.timestamp()),
            "date": index.strftime("%Y-%m-%d"),
            "open": _px(row["Open"]),
            "high": _px(row["High"]),
            "low": _px(row["Low"]),
            "close": close,
            "volume": _int(row["Volume"], 0),
        }
        if symbol is not None:
            record = {"symbol": symbol, **record}
        out.append(record)
    return _sanitize(out)


def get_historical(
    symbol: str, start_date: str, end_date: str, interval: str = "1d"
) -> List[Dict[str, Any]]:
    """Fetch historical OHLCV series for a custom date range."""
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            ticker = yf.Ticker(symbol)
            hist = ticker.history(
                start=start_date, end=end_date, interval=interval, timeout=_NET_TIMEOUT
            )

        return _history_records(hist, symbol=symbol)
    except Exception as e:
        return [{"error": str(e), "symbol": symbol}]


def get_historical_period(
    symbol: str, period: str = "6mo", interval: str = "1d"
) -> List[Dict[str, Any]]:
    """Fetch historical OHLCV data using period codes (e.g. 1mo, 6mo, 1y, 5y)."""
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            ticker = yf.Ticker(symbol)
            hist = ticker.history(
                period=period, interval=interval, timeout=_NET_TIMEOUT
            )

        return _history_records(hist)
    except Exception as e:
        return [{"error": str(e), "symbol": symbol}]


def get_batch_quotes(symbols: List[str]) -> List[Dict[str, Any]]:
    """Fetch provider quotes, preserving market timestamps and per-symbol failures."""
    from concurrent.futures import ThreadPoolExecutor

    if len(symbols) > 50:
        return [{"error": "Request at most 50 symbols per batch"}]
    selected = list(dict.fromkeys(symbols))
    if not selected:
        return []
    with ThreadPoolExecutor(max_workers=min(4, len(selected))) as executor:
        return list(executor.map(get_quote, selected))


def get_info(symbol: str) -> Dict[str, Any]:
    """Fetch company fundamentals, valuation ratios, and corporate profile."""
    try:
        from app.providers import ticker_info

        ticker, info = ticker_info(symbol)
        return _sanitize(
            {
                "symbol": symbol,
                "company_name": info.get("longName") or info.get("shortName", "N/A"),
                "sector": info.get("sector", "N/A"),
                "industry": info.get("industry", "N/A"),
                "currency": info.get("currency"),
                "market_cap": info.get("marketCap"),
                "retrieved_at": datetime.now().astimezone().isoformat(),
                "as_of": info.get("regularMarketTime"),
                "most_recent_quarter": info.get("mostRecentQuarter"),
                "last_fiscal_year_end": info.get("lastFiscalYearEnd"),
                "current_price": info.get("currentPrice")
                or info.get("regularMarketPrice"),
                "trailing_pe": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"),
                "peg_ratio": info.get("trailingPegRatio"),
                "price_to_book": info.get("priceToBook"),
                "dividend_yield": info.get("dividendYield"),
                "beta": info.get("beta"),
                "52w_high": info.get("fiftyTwoWeekHigh"),
                "52w_low": info.get("fiftyTwoWeekLow"),
                "free_cashflow": info.get("freeCashflow"),
                "profit_margins": info.get("profitMargins"),
                "operating_margins": info.get("operatingMargins"),
                "return_on_equity": info.get("returnOnEquity"),
                "return_on_assets": info.get("returnOnAssets"),
                "total_debt": info.get("totalDebt"),
                "total_cash": info.get("totalCash"),
                "short_ratio": info.get("shortRatio"),
                "shares_outstanding": info.get("sharesOutstanding"),
                "summary": info.get("longBusinessSummary", ""),
                "website": info.get("website", ""),
            }
        )
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


def get_financials(symbol: str, frequency: str = "latest") -> Dict[str, Any]:
    """Fetch the latest available statement frequency without mixing annual and quarterly totals."""
    from datetime import timezone

    try:
        if frequency not in {"latest", "annual", "quarterly"}:
            raise ValueError("Use latest, annual or quarterly financial statements")
        from app.providers import ticker_info, refresh_statement_cache

        refresh_statement_cache()
        ticker, info = ticker_info(symbol)

        def records(df):
            if not isinstance(df, pd.DataFrame) or df.empty:
                return {}
            result = {}
            for column in df.columns:
                day = pd.Timestamp(column).date().isoformat()
                result[day] = {
                    str(label): _sanitize(
                        value.item() if hasattr(value, "item") else value
                    )
                    for label, value in df[column].items()
                    if pd.notna(value)
                }
            return result

        def tables(quarterly):
            return {
                "income_statement": records(
                    ticker.quarterly_income_stmt if quarterly else ticker.financials
                ),
                "balance_sheet": records(
                    ticker.quarterly_balance_sheet
                    if quarterly
                    else ticker.balance_sheet
                ),
                "cash_flow": records(
                    ticker.quarterly_cashflow if quarterly else ticker.cashflow
                ),
            }

        fetched = {}
        warnings = []
        for kind in ["annual", "quarterly"] if frequency == "latest" else [frequency]:
            try:
                fetched[kind] = tables(kind == "quarterly")
            except Exception:
                warnings.append(
                    f"{kind.title()} financial statements unavailable from Yahoo Finance"
                )

        def latest(data):
            return max((day for table in data.values() for day in table), default="")

        available = {kind: data for kind, data in fetched.items() if latest(data)}
        if not available:
            raise ValueError("Financial statements unavailable from Yahoo Finance")
        chosen = max(
            available, key=lambda kind: (latest(available[kind]), kind == "quarterly")
        )
        current = datetime.now(timezone.utc)
        from decimal import Decimal
        currency = info.get("financialCurrency")
        report_figures = []
        monetary_fields = {"Total Revenue", "Net Income", "Net Income Common Stockholders",
                           "Total Assets", "Stockholders Equity", "Total Equity Gross Minority Interest",
                           "Operating Cash Flow", "Free Cash Flow", "Total Debt", "Cash And Cash Equivalents"}
        for table_name, table in available[chosen].items():
            for period_end, values in table.items():
                for label, value in values.items():
                    if label not in monetary_fields or value is None:
                        continue
                    amount = Decimal(str(value))
                    if not amount.is_finite():
                        continue
                    report_figures.append({"statement": table_name, "period_end": period_end,
                        "field": label, "amount": format(amount, "f"), "currency": currency,
                        "display": f"{currency or 'Currency unavailable'} {amount:,.2f}"})
        return _sanitize(
            {
                "symbol": symbol,
                "currency": info.get("financialCurrency"),
                **available[chosen],
                "frequency": chosen.title(),
                "amount_unit": "base currency units, not thousands/millions/billions",
                "period_label": "Annual period ending" if chosen == "annual" else "Interim period ending",
                "audit_status": "Not verified from an audited filing",
                "report_figures": report_figures,
                "as_of": latest(available[chosen]),
                "retrieved_at": current.isoformat(),
                "timestamp": int(current.timestamp()),
                "warnings": warnings,
            }
        )
    except Exception as error:
        return {"error": str(error), "symbol": symbol}


# -----------------------------------------------------------------------------
# Portfolio Reconstruction & Back-Projection
# -----------------------------------------------------------------------------


def replay_portfolio_transactions(transactions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compatibility CLI: Rust decimal ledger; never invent a historical NAV."""
    import asyncio
    from app.native_engine import NativeEngine
    from app.portfolio_service import import_trades

    async def calculate():
        engine = NativeEngine()
        try:
            trades = import_trades(
                {"portfolio_name": "CLI", "transactions": transactions}
            )
            return await engine.call(
                os.environ.get("ENGINE_ORG_ID", "local-org"),
                "portfolio_ledger",
                {"opening_positions": [], "transactions": trades},
            )
        finally:
            await engine.close()

    return asyncio.run(calculate())


def main():
    parser = argparse.ArgumentParser(
        description="Standalone yfinance Market & Portfolio CLI"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # quote
    p_quote = subparsers.add_parser("quote")
    p_quote.add_argument("symbol", help="Ticker symbol (e.g. AAPL, NVDA)")

    # batch_quotes
    p_batch = subparsers.add_parser("batch_quotes")
    p_batch.add_argument("symbols", nargs="+", help="Space-separated list of symbols")

    # history
    p_hist = subparsers.add_parser("history")
    p_hist.add_argument("symbol")
    p_hist.add_argument("--period", default="6mo")
    p_hist.add_argument("--interval", default="1d")

    # info
    p_info = subparsers.add_parser("info")
    p_info.add_argument("symbol")

    # financials
    p_fin = subparsers.add_parser("financials")
    p_fin.add_argument("symbol")

    # replay
    p_replay = subparsers.add_parser("replay")
    p_replay.add_argument(
        "transactions_json", help="Path to JSON file containing transactions"
    )

    args = parser.parse_args()

    result = None
    if args.command == "quote":
        result = get_quote(args.symbol)
    elif args.command == "batch_quotes":
        result = get_batch_quotes(args.symbols)
    elif args.command == "history":
        result = get_historical_period(
            args.symbol, period=args.period, interval=args.interval
        )
    elif args.command == "info":
        result = get_info(args.symbol)
    elif args.command == "financials":
        result = get_financials(args.symbol)
    elif args.command == "replay":
        with open(args.transactions_json, "r", encoding="utf-8") as f:
            txns = json.load(f)
        result = replay_portfolio_transactions(txns)

    # Standard JSON dump safely handling NaNs
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
