from typing import Any, cast
import pandas as pd
import numpy as np
import yfinance as yf
from langchain_core.tools import tool

from research_agent.market_data import (
    YFinanceUtils,
)
from research_agent.market_data import (
    build_data_quality_check as _build_data_quality_check,
)
from research_agent.market_data import (
    compact_balance_sheet as _compact_balance_sheet,
)
from research_agent.market_data import (
    compact_cash_flow as _compact_cash_flow,
)
from research_agent.market_data import (
    compact_valuation_ratios as _compact_valuation_ratios,
)
from research_agent.reporting import clean_visible_report
from research_agent.schemas import DCFValuationRequest, ReportFormatResult, TradeLevelsResult
from research_agent.search import build_fast_market_context, build_market_news_context, build_missing_quarter_search
from research_agent.search import cached_official_bursa_filings as _cached_official_bursa_filings
from research_agent.search import search_with_tavily as _search_with_tavily
from research_agent.tickers import resolve_bursa_ticker
from research_agent.valuation import calculate_dcf_valuation_result, calculate_valuation_multiples_result


@tool
def search_bursa_news(company_name: str) -> dict:
    """Search recent Bursa Malaysia company news with Tavily and return a compact summary."""
    query = f"{company_name} Bursa Malaysia financial result target price research report"
    return {
        "query": query,
        "results": [source.model_dump() for source in _search_with_tavily(query, max_results=3)],
    }


@tool
def search_market_context(company_name: str, ticker: str = "", sector: str = "", industry: str = "") -> dict:
    """Search market, macro, industry, and competitor signals that may affect a Bursa company."""
    return build_market_news_context(
        company_name=company_name,
        ticker=ticker or company_name,
        sector=sector,
        industry=industry,
    )


@tool
def normalize_bursa_ticker(symbol_or_name: str) -> str:
    """Resolve a Bursa Malaysia company name, stock code, or ticker into a Yahoo Finance .KL symbol."""
    return resolve_bursa_ticker(symbol_or_name)


@tool
def search_bursa_stock(query: str) -> list[dict]:
    """Search Bursa Malaysia stock candidates by company name or keyword."""
    return YFinanceUtils.search_stock_by_name(query)


@tool
def fetch_bursa_stock_data(ticker_code_or_name: str) -> dict:
    """Fetch a compact Bursa stock snapshot: price, ratios, dividend, market cap, sector, and summary."""
    ticker = resolve_bursa_ticker(ticker_code_or_name)
    result = YFinanceUtils.get_stock_info(ticker)
    if result.get("status") == "FAILED":
        return result
    return {
        "symbol": result.get("symbol", ticker),
        "company_name": result.get("company_name"),
        "sector": result.get("sector"),
        "industry": result.get("industry"),
        "currency": result.get("currency", "MYR"),
        "current_price": result.get("current_price"),
        "pe_ratio": result.get("pe_ratio"),
        "forward_pe": result.get("forward_pe"),
        "dividend_yield": result.get("dividend_yield"),
        "price_to_book": result.get("price_to_book"),
        "price_to_sales": result.get("price_to_sales"),
        "enterprise_to_ebitda": result.get("enterprise_to_ebitda"),
        "trailing_eps": result.get("trailing_eps"),
        "book_value": result.get("book_value"),
        "fifty_two_week_high": result.get("fifty_two_week_high"),
        "fifty_two_week_low": result.get("fifty_two_week_low"),
        "market_cap": result.get("market_cap"),
        "enterprise_value": result.get("enterprise_value"),
        "summary": result.get("summary"),
    }


@tool
def fetch_bursa_quarterly_reports(ticker_code_or_name: str) -> dict:
    """Fetch compact recent quarterly revenue, EBITDA, operating income, net income, and EPS for a Bursa stock."""
    ticker = resolve_bursa_ticker(ticker_code_or_name)
    result = YFinanceUtils.get_quarterly_reports(ticker_code_or_name)
    return {
        "symbol": result.get("symbol", ticker),
        "company_name": result.get("company_name"),
        "quarterly_financials": result.get("quarterly_financials", {}),
    }


@tool
def build_bursa_research_snapshot(ticker_code_or_name: str) -> dict:
    """Build one compact report-ready snapshot with financial statements, ratios, valuation, trade levels, and developments.

    Accepts company names (e.g. 'Focus Point', 'Genting') or numeric codes (e.g. '0157', '3182').
    """
    # 1. ALWAYS RESOLVE TICKER FIRST
    ticker = resolve_bursa_ticker(ticker_code_or_name)
    
    # 2. Pass the resolved 4-digit .KL ticker to data fetchers
    stock = cast(Any, fetch_bursa_stock_data).func(ticker) or {}
    quarters = cast(Any, fetch_bursa_quarterly_reports).func(ticker) or {}
    technicals = cast(Any, fetch_bursa_technical_indicators).func(ticker) or {}

    stock_obj = yf.Ticker(ticker)
    quarterly_summary = quarters.get("quarterly_financials") or {}
    balance_sheet = _compact_balance_sheet(stock_obj) or {}
    cash_flow = _compact_cash_flow(stock_obj) or {}
    valuation_ratios = _compact_valuation_ratios(stock) or {}

    complete_periods = _complete_statement_periods(
        quarterly_summary=quarterly_summary,
        balance_sheet=balance_sheet,
        cash_flow=cash_flow,
    )
    financial_table_markdown = _build_last_4q_financial_table(
        quarterly_summary=quarterly_summary,
        balance_sheet=balance_sheet,
        cash_flow=cash_flow,
        valuation_ratios=valuation_ratios,
    )
    
    net_incomes = [
        v.get("net_income_myr_m")
        for v in quarterly_summary.values()
        if isinstance(v.get("net_income_myr_m"), (int, float))
    ]
    
    valuation = calculate_dcf_valuation_result(
        current_price=stock.get("current_price"),
        pe_ratio=stock.get("pe_ratio"),
        forward_pe=stock.get("forward_pe"),
        trailing_eps=stock.get("trailing_eps"),
        book_value=stock.get("book_value"),
        dividend_yield_pct=stock.get("dividend_yield"),
        quarterly_net_income=net_incomes,
    )

    company = stock.get("company_name") or ticker_code_or_name
    market_context = build_fast_market_context(
        company_name=company,
        ticker=ticker,
        sector=stock.get("sector") or "",
        industry=stock.get("industry") or "",
    )
    sector_insight = _build_sector_insight(stock=stock, market_context=market_context)
    forecast_explanation = _build_forecast_explanation(stock=stock, valuation=valuation)

    return {
        "stock": stock,
        "financial_statement_table_markdown": financial_table_markdown,
        "valuation": {
            "target_price_myr": valuation.get("estimated_fair_value_myr"),
            "upside_downside_pct": valuation.get("upside_downside_pct"),
            "growth_rate": valuation.get("growth_rate"),
            "derived_growth_rate_pct": valuation.get("derived_growth_rate_pct"),
            "growth_source": valuation.get("growth_source"),
            "wacc": valuation.get("wacc"),
            "terminal_pe": valuation.get("terminal_pe"),
            "eps_input": valuation.get("eps_input"),
        },
        "trade_levels": {
            "support_level": technicals.get("support_level"),
            "resistance_level": technicals.get("resistance_level"),
            "buy_range": f"RM {technicals.get('buy_range_min')} - RM {technicals.get('buy_range_max')}",
            "sell_range": f"RM {technicals.get('sell_range_min')} - RM {technicals.get('sell_range_max')}",
            "rsi": technicals.get("rsi"),
            "rsi_signal": technicals.get("rsi_signal"),
            "ema_trend": technicals.get("ema_trend"),
        },
        "sector_insight": sector_insight,
        "recent_developments": market_context.get("market_news", {}).get("items", [])[:2],
    }
    
@tool
def calculate_valuation_multiples(price: float, eps: float, bvps: float) -> dict:
    """Calculate key valuation metrics (P/E Ratio and P/B Ratio) for financial analysis."""
    return calculate_valuation_multiples_result(price=price, eps=eps, bvps=bvps)

def _build_forecast_explanation(stock: dict, valuation: dict) -> dict:
    growth = valuation.get("growth_rate")
    growth_pct = (growth * 100) if growth is not None else 6.0
    wacc = valuation.get("wacc") or 0.08

    return {
        "method": valuation.get("valuation_method", "5-year earnings-proxy DCF"),
        "base_eps_myr": valuation.get("eps_input") or stock.get("trailing_eps"),
        "assumed_growth_rate_pct": round(growth_pct, 2),
        "wacc_pct": round(wacc * 100, 2),
        "terminal_pe_multiple": valuation.get("terminal_pe"),
        "intrinsic_fair_value_myr": valuation.get("estimated_fair_value_myr"),
        "implied_upside_pct": valuation.get("upside_downside_pct"),
        "growth_source": valuation.get("growth_source", "Macro Benchmark"),
    }
    


def _build_last_4q_financial_table(
    quarterly_summary: dict | None,
    balance_sheet: dict | None,
    cash_flow: dict | None,
    valuation_ratios: dict | None,
) -> str:
    # Safely convert any None inputs to empty dictionaries
    qs = quarterly_summary or {}
    bs = balance_sheet or {}
    cf = cash_flow or {}
    vr = valuation_ratios or {}

    periods = list(dict.fromkeys([*qs.keys(), *bs.keys(), *cf.keys()]))[:4]
    while len(periods) < 4:
        periods.append(f"Q-{len(periods)}")

    rows = [
        ("Revenue (RMm)", qs, "revenue_myr_m"),
        ("Net Income (RMm)", qs, "net_income_myr_m"),
        ("Diluted EPS (RM)", qs, "diluted_eps"),
        ("Cash (RMm)", bs, "cash_myr_m"),
        ("Total Assets (RMm)", bs, "total_assets_myr_m"),
        ("Total Debt (RMm)", bs, "total_debt_myr_m"),
        ("Shareholders' Equity (RMm)", bs, "shareholders_equity_myr_m"),
        ("Operating Cash Flow (RMm)", cf, "operating_cash_flow_myr_m"),
        ("Free Cash Flow (RMm)", cf, "free_cash_flow_myr_m"),
    ]
    ratio_rows = [
        ("P/E (trailing)", vr.get("pe"), "x"),
        ("Forward P/E", vr.get("forward_pe"), "x"),
        ("Price-to-Book", vr.get("price_to_book"), "x"),
        ("Price-to-Sales", vr.get("price_to_sales"), "x"),
        ("Dividend Yield", vr.get("dividend_yield_pct"), "%"),
        ("Market Capitalisation", vr.get("market_cap_myr_m"), "RMm"),
        ("Enterprise Value", vr.get("enterprise_value_myr_m"), "RMm"),
    ]

    quarterly_table = [
        f"| Metric (Last 4Q) | {' | '.join(periods)} |",
        "| --- | --- | --- | --- | --- |",
    ]
    for label, source, key in rows:
        values = [_format_table_value(source.get(period, {}).get(key)) for period in periods]
        quarterly_table.append(f"| {label} | {' | '.join(values)} |")

    latest_ratio_table = [
        "**Latest Valuation Ratios**",
        "| Metric | Latest |",
        "| --- | --- |",
    ]
    for label, value, unit in ratio_rows:
        latest_ratio_table.append(f"| {label} | {_format_latest_ratio_value(value, unit)} |")

    return "\n\n".join(("\n".join(quarterly_table), "\n".join(latest_ratio_table)))


def _complete_statement_periods(
    quarterly_summary: dict | None,
    balance_sheet: dict | None,
    cash_flow: dict | None,
) -> list[str]:
    qs = quarterly_summary or {}
    bs = balance_sheet or {}
    cf = cash_flow or {}

    periods = list(dict.fromkeys([*qs.keys(), *bs.keys(), *cf.keys()]))[:4]
    complete_periods = []
    for period in periods:
        if period in qs and period in bs and period in cf:
            complete_periods.append(period)
    return complete_periods

def _format_table_value(value: Any) -> str:
    if value in (None, ""):
        return "N/A"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)

def _clean_headline(title: str) -> str:
    """Strip website domain names and generic stock quote suffixes."""
    ignore_phrases = ["Stock Price", "Quote & History", "Live - Investing.com", "KLSE Screener"]
    for phrase in ignore_phrases:
        if phrase in title:
            return ""
    return title.strip()

def _build_sector_insight(stock: dict, market_context: dict) -> dict:
    sector = stock.get("sector") or "N/A"
    industry = stock.get("industry") or "N/A"
    
    market_items = market_context.get("market_news", {}).get("items", [])
    macro_items = market_context.get("macro_news", {}).get("items", [])
    
    raw_titles = [item.get("title", "") for item in [*market_items, *macro_items]]
    cleaned_watch_items = [t for t in (_clean_headline(t) for t in raw_titles) if t]
    
    if not cleaned_watch_items:
        cleaned_watch_items = [
            f"Regulatory & tariff outlook in {sector}",
            f"Capital expenditure & leverage management in {industry}",
            "Input cost volatility and interest rate movements"
        ]

    return {
        "sector": sector,
        "industry": industry,
        "business_exposure": (stock.get("summary") or "")[:150],
        "demand_drivers": [
            f"Secular demand trends across {sector}",
            f"Competitive positioning in {industry}",
            "Operational efficiency and pricing power"
        ],
        "watch_items": cleaned_watch_items[:3],
        "retrieved_signals": market_items[:2],
    }

def _format_latest_ratio_value(value: Any, unit: str) -> str:
    formatted = _format_table_value(value)
    if formatted == "N/A":
        return formatted
    if unit in {"x", "%"}:
        return f"{formatted}{unit}"
    if unit == "RMm":
        return f"RM {formatted}m"
    return formatted

@tool
def fetch_bursa_technical_indicators(symbol: str) -> dict:
    """Fetch historical price data and calculate technical indicators (RSI, Bollinger Bands, ATR, Support/Resistance)."""
    ticker_code = resolve_bursa_ticker(symbol)
    try:
        df = yf.Ticker(ticker_code).history(period="6mo", interval="1d")
        if df.empty or len(df) < 20:
            return {"error": f"Insufficient historical data for {symbol}"}

        close = df["Close"]
        high = df["High"]
        low = df["Low"]

        current_price = round(float(close.iloc[-1]), 3)

        # 1. Moving Averages & Bands
        ema_20 = round(float(close.ewm(span=20).mean().iloc[-1]), 3)
        ema_50 = round(float(close.ewm(span=50).mean().iloc[-1]), 3)
        sma_20 = close.rolling(window=20).mean().iloc[-1]
        std_20 = close.rolling(window=20).std().iloc[-1]
        bb_upper = round(float(sma_20 + (2 * std_20)), 3)
        bb_lower = round(float(sma_20 - (2 * std_20)), 3)

        # 2. Average True Range (ATR)
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = round(float(tr.rolling(14).mean().iloc[-1]), 3)

        # 3. Relative Strength Index (RSI 14)
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
        rs = gain / loss
        rsi_series = 100 - (100 / (1 + rs))
        rsi = round(float(rsi_series.iloc[-1]), 2)

        # 4. Support & Resistance
        support_level = round(float(low.tail(30).min()), 3)
        resistance_level = round(float(high.tail(30).max()), 3)

        # 5. Buy and Sell Price Ranges (Sanitized)
        buy_min = max(0.01, round(min(support_level, bb_lower), 3))
        buy_max = round(min(current_price, ema_20), 3)
        if buy_max < buy_min:
            buy_max = round(buy_min + atr, 3)

        sell_min = round(max(current_price * 1.03, bb_upper), 3)
        sell_max = round(max(resistance_level + atr, sell_min + (2 * atr)), 3)

        # Signals
        rsi_signal = "overbought" if current_price > bb_upper else ("oversold" if current_price < bb_lower else "neutral")
        ema_trend = "bullish" if ema_20 > ema_50 else "bearish"

        return TradeLevelsResult(
            symbol=ticker_code,
            current_price=current_price,
            support_level=support_level,
            resistance_level=resistance_level,
            buy_range_min=buy_min,
            buy_range_max=buy_max,
            sell_range_min=sell_min,
            sell_range_max=sell_max,
            rsi=rsi,
            rsi_signal=rsi_signal,
            ema_trend=ema_trend,
            atr=atr,
        ).model_dump()

    except Exception as exc:
        return {"error": f"Failed to compute technical levels for {symbol}: {str(exc)}"}
    
@tool
def search_official_bursa_filings(company_name_or_ticker: str = "" ) -> dict:
    """Search official Bursa Malaysia announcements and company investor-relations pages before generic sources."""
    query = company_name_or_ticker 
    return _cached_official_bursa_filings(query)



@tool(args_schema=DCFValuationRequest)
def calculate_dcf_valuation(
    current_price: float | None,
    pe_ratio: float | None = None,
    growth_rate: float = 0.08,
    wacc: float = 0.08,
    terminal_growth_rate: float = 0.02,
) -> dict:
    """Compute a conservative 5-year earnings proxy valuation in MYR."""
    return calculate_dcf_valuation_result(
        current_price=current_price,
        pe_ratio=pe_ratio,
        growth_rate=growth_rate,
      
    )


@tool
def format_equity_report(title: str, body_markdown: str) -> dict:
    """Attach a standard education-only disclaimer to a Markdown equity research report."""
    disclaimer = "This research is for education only and is not personal financial advice."
    report = clean_visible_report(body_markdown)
    if not report.startswith("#"):
        report = f"# {title.strip()}\n\n{report}"
    return ReportFormatResult(report_markdown=report, disclaimer=disclaimer, status="SUCCESS").model_dump()

