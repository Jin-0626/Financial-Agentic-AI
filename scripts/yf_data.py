"""
Market Data & Portfolio Analytics Engine (yfinance)
Standalone module for CLI use, web APIs (FastAPI/Flask), or AI agent tool calling.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import math
import os
import re
import sys
from datetime import date as _date, datetime, timedelta
from typing import Any, Dict, List, Optional, Union

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
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            ticker = yf.Ticker(symbol)
            info = ticker.info or {}
            hist = ticker.history(period="1d", timeout=_NET_TIMEOUT)

        if hist.empty:
            return {"error": "No data available", "symbol": symbol}

        current_price = float(hist["Close"].iloc[-1])
        previous_close = float(info.get("previousClose", current_price))
        change = current_price - previous_close
        change_pct = (change / previous_close) * 100 if previous_close else 0.0

        d = _price_digits(current_price)
        return _sanitize({
            "symbol": symbol,
            "price": _px(current_price, d),
            "change": _px(change, d),
            "change_percent": round(change_pct, 2),
            "volume": _int(hist["Volume"].iloc[-1]) if not hist["Volume"].empty else None,
            "high": _px(hist["High"].iloc[-1], d) if not hist["High"].empty else None,
            "low": _px(hist["Low"].iloc[-1], d) if not hist["Low"].empty else None,
            "open": _px(hist["Open"].iloc[-1], d) if not hist["Open"].empty else None,
            "previous_close": _px(previous_close, d),
            "timestamp": int(datetime.now().timestamp()),
            "exchange": info.get("exchange", "")
        })
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


def get_historical(symbol: str, start_date: str, end_date: str, interval: str = "1d") -> List[Dict[str, Any]]:
    """Fetch historical OHLCV series for a custom date range."""
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            ticker = yf.Ticker(symbol)
            hist = ticker.history(start=start_date, end=end_date, interval=interval, timeout=_NET_TIMEOUT)

        if hist.empty:
            return []

        out = []
        for index, row in hist.iterrows():
            close = _px(row["Close"])
            if close is None:
                continue
            out.append({
                "symbol": symbol,
                "timestamp": int(index.timestamp()),
                "date": index.strftime("%Y-%m-%d"),
                "open": _px(row["Open"]),
                "high": _px(row["High"]),
                "low": _px(row["Low"]),
                "close": close,
                "volume": _int(row["Volume"], 0),
            })
        return _sanitize(out)
    except Exception as e:
        return [{"error": str(e), "symbol": symbol}]


def get_historical_period(symbol: str, period: str = "6mo", interval: str = "1d") -> List[Dict[str, Any]]:
    """Fetch historical OHLCV data using period codes (e.g. 1mo, 6mo, 1y, 5y)."""
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period=period, interval=interval, timeout=_NET_TIMEOUT)

        if hist.empty:
            return []

        out = []
        for index, row in hist.iterrows():
            close = _px(row["Close"])
            if close is None:
                continue
            out.append({
                "timestamp": int(index.timestamp()),
                "date": index.strftime("%Y-%m-%d"),
                "open": _px(row["Open"]),
                "high": _px(row["High"]),
                "low": _px(row["Low"]),
                "close": close,
                "volume": _int(row["Volume"], 0)
            })
        return _sanitize(out)
    except Exception as e:
        return [{"error": str(e), "symbol": symbol}]


def get_batch_quotes(symbols: List[str]) -> List[Dict[str, Any]]:
    """Batch-download latest quotes for multiple symbols in a single roundtrip."""
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            data = yf.download(
                symbols,
                period="5d",
                group_by="ticker",
                progress=False,
                threads=True,
                auto_adjust=True,
                timeout=_NET_TIMEOUT
            )

        if data is None or data.empty:
            return []

        results = []
        for sym in symbols:
            try:
                if not isinstance(data.columns, pd.MultiIndex):
                    hist = data
                else:
                    level0 = data.columns.get_level_values(0).unique().tolist()
                    level1 = data.columns.get_level_values(1).unique().tolist()
                    if sym in level0:
                        hist = data[sym]
                    elif sym in level1:
                        hist = data.xs(sym, axis=1, level=1)
                    else:
                        continue

                hist = hist.dropna(how="all")
                if hist.empty:
                    continue

                raw_price = hist["Close"].iloc[-1]
                if pd.isna(raw_price):
                    continue

                current_price = float(raw_price)
                raw_prev = hist["Close"].iloc[-2] if len(hist) >= 2 else raw_price
                prev_close = float(raw_prev) if not pd.isna(raw_prev) else current_price
                change = current_price - prev_close
                change_pct = (change / prev_close) * 100 if prev_close else 0.0

                d = _price_digits(current_price)
                results.append({
                    "symbol": sym,
                    "price": _px(current_price, d),
                    "change": _px(change, d),
                    "change_percent": round(change_pct, 2),
                    "volume": _int(hist["Volume"].iloc[-1], 0) if "Volume" in hist else 0,
                    "high": _px(hist["High"].iloc[-1], d) if "High" in hist else None,
                    "low": _px(hist["Low"].iloc[-1], d) if "Low" in hist else None,
                    "open": _px(hist["Open"].iloc[-1], d) if "Open" in hist else None,
                    "previous_close": _px(prev_close, d),
                    "timestamp": int(datetime.now().timestamp()),
                })
            except Exception:
                continue

        return _sanitize(results)
    except Exception as e:
        return [{"error": str(e)}]


def get_info(symbol: str) -> Dict[str, Any]:
    """Fetch company fundamentals, valuation ratios, and corporate profile."""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        return _sanitize({
            "symbol": symbol,
            "company_name": info.get("longName") or info.get("shortName", "N/A"),
            "sector": info.get("sector", "N/A"),
            "industry": info.get("industry", "N/A"),
            "currency": info.get("currency", "USD"),
            "market_cap": info.get("marketCap"),
            "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
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
        })
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


def get_financials(symbol: str) -> Dict[str, Any]:
    """Fetch annual balance sheet, income statement, and cash flow tables."""
    try:
        ticker = yf.Ticker(symbol)

        def _df_to_records(df: Optional[pd.DataFrame]) -> Dict[str, Any]:
            if df is None or df.empty:
                return {}
            out: Dict[str, Any] = {}
            for col in df.columns:
                col_key = col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col)
                out[col_key] = {}
                for idx in df.index:
                    val = df.loc[idx, col]
                    if pd.notna(val):
                        out[col_key][str(idx)] = float(val) if isinstance(val, (int, float)) else str(val)
            return out

        return _sanitize({
            "symbol": symbol,
            "income_statement": _df_to_records(ticker.financials),
            "balance_sheet": _df_to_records(ticker.balance_sheet),
            "cash_flow": _df_to_records(ticker.cashflow),
            "timestamp": int(datetime.now().timestamp()),
        })
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


# -----------------------------------------------------------------------------
# Portfolio Reconstruction & Back-Projection
# -----------------------------------------------------------------------------

def _candidate_yf_symbols(symbol: str) -> List[str]:
    """Build candidate ticker list covering common international exchange suffixes."""
    s = symbol.strip().upper()
    if not s:
        return []
    if "." in s or s.startswith("^") or "-" in s or "=" in s:
        return [s]
    return [s, f"{s}.TO", f"{s}.NS", f"{s}.BO", f"{s}.L", f"{s}.AX", f"{s}.HK", f"{s}.KL"]


def _resolve_tickers(symbols: List[str], period: str = "1y") -> Dict[str, tuple[str, pd.Series]]:
    """Resolves arbitrary user symbols to verified yfinance series."""
    candidates_per_sym = {s: _candidate_yf_symbols(s) for s in symbols}
    all_candidates = sorted({c for variants in candidates_per_sym.values() for c in variants})

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        data = yf.download(
            tickers=all_candidates,
            period=period,
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
            timeout=_NET_TIMEOUT,
        )

    if data is None or data.empty:
        return {}

    single = len(all_candidates) == 1

    def _extract_closes(ticker: str) -> Optional[pd.Series]:
        try:
            if single:
                return data["Close"].dropna()
            return data[ticker]["Close"].dropna()
        except Exception:
            return None

    resolved = {}
    for sym, variants in candidates_per_sym.items():
        for candidate in variants:
            series = _extract_closes(candidate)
            if series is not None and not series.empty:
                resolved[sym] = (candidate, series)
                break
    return resolved


def replay_portfolio_transactions(transactions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Replay a list of buy/sell transactions to compute true historical NAV and Cost Basis.
    
    Transactions format:
      [{"date": "YYYY-MM-DD", "symbol": "AAPL", "type": "BUY", "quantity": 10, "price": 150.0}, ...]
    """
    try:
        txns = [
            t for t in transactions
            if str(t.get("type", "")).upper() in ("BUY", "SELL")
            and t.get("symbol") and t.get("date")
        ]
        if not txns:
            return {"dates": [], "navs": [], "costs": []}

        txns.sort(key=lambda t: t["date"])
        symbols = sorted({t["symbol"] for t in txns})

        first_date = pd.Timestamp(txns[0]["date"]).normalize()
        today = pd.Timestamp(_date.today()).normalize()
        if first_date > today:
            return {"error": "Earliest transaction date is in the future"}

        days_back = (today - first_date).days + 7
        if days_back <= 31:
            period = "1mo"
        elif days_back <= 180:
            period = "6mo"
        elif days_back <= 365:
            period = "1y"
        elif days_back <= 730:
            period = "2y"
        elif days_back <= 1825:
            period = "5y"
        else:
            period = "max"

        resolved = _resolve_tickers(symbols, period=period)
        if not resolved:
            return {"error": "No price history found for transaction symbols", "missing": symbols}

        closes_df = pd.DataFrame({s: ser for s, (_yf_sym, ser) in resolved.items()}).sort_index().ffill()
        closes_df = closes_df[closes_df.index >= first_date]

        if closes_df.empty:
            return {"error": "No market trading days found in specified window"}

        positions = {s: 0.0 for s in symbols}
        wac_cost = {s: 0.0 for s in symbols}
        running_cost_basis = 0.0
        tx_idx, n_tx = 0, len(txns)

        out_dates, out_navs, out_costs = [], [], []

        for ts, row in closes_df.iterrows():
            day = ts.date().isoformat()

            while tx_idx < n_tx and txns[tx_idx]["date"] <= day:
                tx = txns[tx_idx]
                s = tx["symbol"]
                qty = float(tx.get("quantity", 0))
                price = float(tx.get("price", 0))
                kind = str(tx.get("type")).upper()

                if kind == "BUY":
                    new_qty = positions[s] + qty
                    if new_qty > 0:
                        wac_cost[s] = (positions[s] * wac_cost[s] + qty * price) / new_qty
                    positions[s] = new_qty
                    running_cost_basis += qty * price
                elif kind == "SELL":
                    sell_qty = min(qty, positions[s])
                    running_cost_basis -= sell_qty * wac_cost[s]
                    positions[s] -= sell_qty
                    if positions[s] <= 1e-9:
                        positions[s] = 0.0
                        wac_cost[s] = 0.0
                tx_idx += 1

            nav = sum(
                float(row[s]) * positions[s]
                for s in symbols
                if s in row.index and pd.notna(row[s])
            )

            out_dates.append(day)
            out_navs.append(round(nav, 2))
            out_costs.append(round(running_cost_basis, 2))

        return _sanitize({
            "dates": out_dates,
            "navs": out_navs,
            "costs": out_costs,
            "resolved": {s: r[0] for s, r in resolved.items()},
            "missing": [s for s in symbols if s not in resolved],
        })
    except Exception as e:
        return {"error": str(e)}


# -----------------------------------------------------------------------------
# CLI Dispatcher
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Standalone yfinance Market & Portfolio CLI")
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
    p_replay.add_argument("transactions_json", help="Path to JSON file containing transactions")

    args = parser.parse_args()

    result = None
    if args.command == "quote":
        result = get_quote(args.symbol)
    elif args.command == "batch_quotes":
        result = get_batch_quotes(args.symbols)
    elif args.command == "history":
        result = get_historical_period(args.symbol, period=args.period, interval=args.interval)
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