from typing import Any, cast

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
from research_agent.schemas import DCFValuationRequest, ReportFormatResult
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
    result = YFinanceUtils.get_stock_info(ticker_code_or_name)
    if result.get("status") == "FAILED":
        return result
    return {
        "symbol": result.get("symbol"),
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
    result = YFinanceUtils.get_quarterly_reports(ticker_code_or_name)
    return {
        "symbol": result.get("symbol"),
        "company_name": result.get("company_name"),
        "quarterly_financials": result.get("quarterly_financials", {}),
    }


@tool
def build_bursa_research_snapshot(ticker_code_or_name: str) -> dict:
    """Build one compact report-ready snapshot with financial statements, ratios, valuation, and developments."""
    stock = cast(Any, fetch_bursa_stock_data).func(ticker_code_or_name)
    quarters = cast(Any, fetch_bursa_quarterly_reports).func(ticker_code_or_name)
    ticker = stock.get("symbol") or resolve_bursa_ticker(ticker_code_or_name)
    stock_obj = yf.Ticker(ticker)
    quarterly_summary = quarters.get("quarterly_financials", {})
    balance_sheet = _compact_balance_sheet(stock_obj)
    cash_flow = _compact_cash_flow(stock_obj)
    valuation_ratios = _compact_valuation_ratios(stock)
    complete_periods = _complete_statement_periods(
        quarterly_summary=quarterly_summary,
        balance_sheet=balance_sheet,
        cash_flow=cash_flow,
    )
    missing_quarter_retry = build_missing_quarter_search(
        company_name=stock.get("company_name") or ticker_code_or_name,
        ticker=ticker,
        known_periods=complete_periods,
    )
    financial_table_markdown = _build_last_4q_financial_table(
        quarterly_summary=quarterly_summary,
        balance_sheet=balance_sheet,
        cash_flow=cash_flow,
        valuation_ratios=valuation_ratios,
    )
    valuation = calculate_dcf_valuation_result(
        current_price=stock.get("current_price"),
        pe_ratio=stock.get("pe_ratio"),
        growth_rate=0.05,
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
        "data_quality": _build_data_quality_check(
            stock=stock,
            quarterly_summary=quarterly_summary,
            balance_sheet=balance_sheet,
            cash_flow=cash_flow,
            valuation_ratios=valuation_ratios,
        ),
        "income_statement": quarterly_summary,
        "quarterly_financial_summary": quarterly_summary,
        "balance_sheet": balance_sheet,
        "cash_flow": cash_flow,
        "valuation_ratios": valuation_ratios,
        "missing_quarter_retry": missing_quarter_retry,
        "financial_statement_table_markdown": financial_table_markdown,
        "quarters": quarterly_summary,
        "valuation": {
            "target_price_myr": valuation.get("estimated_fair_value_myr"),
            "upside_downside_pct": valuation.get("upside_downside_pct"),
            "growth_rate": valuation.get("growth_rate"),
            "wacc": valuation.get("wacc"),
            "terminal_pe": valuation.get("terminal_pe"),
            "eps_input": valuation.get("eps_input"),
        },
        "forecast_explanation": forecast_explanation,
        "sector_insight": sector_insight,
        "recent_developments": market_context.get("market_news", {}).get("items", []),
        "market_context": market_context,
    }


def _build_sector_insight(stock: dict, market_context: dict) -> dict:
    sector = stock.get("sector") or "N/A"
    industry = stock.get("industry") or "N/A"
    market_items = market_context.get("market_news", {}).get("items", [])
    macro_items = market_context.get("macro_news", {}).get("items", [])
    return {
        "sector": sector,
        "industry": industry,
        "business_exposure": (stock.get("summary") or "")[:120],
        "demand_drivers": [
            "consumer health spending",
            "store network productivity",
            "pricing and procurement discipline",
        ],
        "watch_items": [
            "consumer discretionary slowdown",
            "imported lens or equipment cost pressure",
            "retail competition and rental/labour costs",
        ],
        "retrieved_signals": [*market_items[:1], *macro_items[:1]],
    }


def _build_forecast_explanation(stock: dict, valuation: dict) -> dict:
    return {
        "method": "earnings-proxy DCF using current price, trailing P/E, assumed EPS growth, WACC, and terminal P/E",
        "base_eps_myr": valuation.get("eps_input") or stock.get("trailing_eps"),
        "growth_rate": valuation.get("growth_rate"),
        "wacc": valuation.get("wacc"),
        "terminal_pe": valuation.get("terminal_pe"),
        "target_price_myr": valuation.get("estimated_fair_value_myr"),
        "upside_downside_pct": valuation.get("upside_downside_pct"),
        "forecast_message": (
            "Target price rises when earnings growth or terminal multiple improves, and falls when WACC rises or "
            "earnings momentum weakens."
        ),
    }


def _build_last_4q_financial_table(
    quarterly_summary: dict,
    balance_sheet: dict,
    cash_flow: dict,
    valuation_ratios: dict,
) -> str:
    periods = list(dict.fromkeys([*quarterly_summary.keys(), *balance_sheet.keys(), *cash_flow.keys()]))[:4]
    while len(periods) < 4:
        periods.append(f"Q-{len(periods)}")

    rows = [
        ("Revenue (RMm)", quarterly_summary, "revenue_myr_m"),
        ("Net Income (RMm)", quarterly_summary, "net_income_myr_m"),
        ("Diluted EPS (RM)", quarterly_summary, "diluted_eps"),
        ("Cash (RMm)", balance_sheet, "cash_myr_m"),
        ("Total Assets (RMm)", balance_sheet, "total_assets_myr_m"),
        ("Total Debt (RMm)", balance_sheet, "total_debt_myr_m"),
        ("Shareholders' Equity (RMm)", balance_sheet, "shareholders_equity_myr_m"),
        ("Operating Cash Flow (RMm)", cash_flow, "operating_cash_flow_myr_m"),
        ("Free Cash Flow (RMm)", cash_flow, "free_cash_flow_myr_m"),
    ]
    ratio_rows = [
        ("P/E (trailing)", valuation_ratios.get("pe"), "x"),
        ("Forward P/E", valuation_ratios.get("forward_pe"), "x"),
        ("Price-to-Book", valuation_ratios.get("price_to_book"), "x"),
        ("Price-to-Sales", valuation_ratios.get("price_to_sales"), "x"),
        ("Dividend Yield", valuation_ratios.get("dividend_yield_pct"), "%"),
        ("Market Capitalisation", valuation_ratios.get("market_cap_myr_m"), "RMm"),
        ("Enterprise Value", valuation_ratios.get("enterprise_value_myr_m"), "RMm"),
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


def _complete_statement_periods(quarterly_summary: dict, balance_sheet: dict, cash_flow: dict) -> list[str]:
    periods = list(dict.fromkeys([*quarterly_summary.keys(), *balance_sheet.keys(), *cash_flow.keys()]))[:4]
    complete_periods = []
    for period in periods:
        if period in quarterly_summary and period in balance_sheet and period in cash_flow:
            complete_periods.append(period)
    return complete_periods


def _format_table_value(value: Any) -> str:
    if value in (None, ""):
        return "N/A"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


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
def search_official_bursa_filings(company_name_or_ticker: str = "", company_name_or_tticker: str = "") -> dict:
    """Search official Bursa Malaysia announcements and company investor-relations pages before generic sources."""
    query = company_name_or_ticker or company_name_or_tticker
    return _cached_official_bursa_filings(query)


@tool
def calculate_valuation_multiples(price: float, eps: float, bvps: float) -> dict:
    """Calculate key valuation metrics (P/E Ratio and P/B Ratio) for financial analysis."""
    return calculate_valuation_multiples_result(price=price, eps=eps, bvps=bvps)


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
        wacc=wacc,
        terminal_growth_rate=terminal_growth_rate,
    )


@tool
def format_equity_report(title: str, body_markdown: str) -> dict:
    """Attach a standard education-only disclaimer to a Markdown equity research report."""
    disclaimer = "This research is for education only and is not personal financial advice."
    report = clean_visible_report(body_markdown)
    if not report.startswith("#"):
        report = f"# {title.strip()}\n\n{report}"
    return ReportFormatResult(report_markdown=report, disclaimer=disclaimer, status="SUCCESS").model_dump()
