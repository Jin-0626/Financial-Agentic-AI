import math
from typing import Any
import numpy as np
import pandas as pd
import yfinance as yf

from research_agent.schemas import QuarterlyReportResult, StockInfoResult
from research_agent.search import cached_official_bursa_filings
from research_agent.tickers import resolve_bursa_ticker

MIN_SEARCH_QUERY_LENGTH = 2
StatementData = pd.DataFrame | None
StatementFields = dict[str, str]
CompactStatement = dict[str, dict[str, float]]
StockPayload = dict[str, Any]


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def finite_positive_market_number(value: Any) -> float | None:
    number = finite_number(value)
    if number is None or number <= 0:
        return None
    return number


def rounded_positive(value: Any, digits: int) -> float | None:
    number = finite_positive_market_number(value)
    return round(number, digits) if number is not None else None


def compact_statement(statement: StatementData, fields: StatementFields, *, max_periods: int = 3) -> CompactStatement:
    if statement is None or getattr(statement, "empty", True):
        return {}

    compact: CompactStatement = {}
    for period, rows in statement.iloc[:, :max_periods].to_dict().items():
        period_key = str(period)[:10]
        period_values: dict[str, float] = {}
        for source_key, output_key in fields.items():
            number = finite_number(rows.get(source_key))
            if number is None:
                continue
            period_values[output_key] = (
                round(number / 1_000_000, 2) if output_key.endswith("_myr_m") else round(number, 4)
            )
        if period_values:
            compact[period_key] = period_values
    return compact


def compact_quarterly_financials(q_financials: StatementData) -> CompactStatement:
    return compact_statement(
        q_financials,
        {
            "Total Revenue": "revenue_myr_m",
            "EBITDA": "ebitda_myr_m",
            "Operating Income": "operating_income_myr_m",
            "Net Income": "net_income_myr_m",
            "Diluted EPS": "diluted_eps",
            "Basic EPS": "basic_eps",
        },
        max_periods=4,
    )


def compact_balance_sheet(stock: Any) -> CompactStatement:
    fields = {
        "Cash And Cash Equivalents": "cash_myr_m",
        "Cash Cash Equivalents And Short Term Investments": "cash_and_st_investments_myr_m",
        "Total Assets": "total_assets_myr_m",
        "Total Debt": "total_debt_myr_m",
        "Net Debt": "net_debt_myr_m",
        "Stockholders Equity": "shareholders_equity_myr_m",
        "Total Equity Gross Minority Interest": "total_equity_myr_m",
        "Current Assets": "current_assets_myr_m",
        "Current Liabilities": "current_liabilities_myr_m",
    }
    for attribute in ("quarterly_balance_sheet", "balance_sheet"):
        try:
            compact = compact_statement(getattr(stock, attribute), fields, max_periods=4)
        except Exception:  # noqa: BLE001 - yfinance raises provider/version-specific exceptions here.
            continue
        if compact:
            return compact
    return {}


def compact_cash_flow(stock: Any) -> CompactStatement:
    fields = {
        "Operating Cash Flow": "operating_cash_flow_myr_m",
        "Free Cash Flow": "free_cash_flow_myr_m",
        "Capital Expenditure": "capital_expenditure_myr_m",
        "Dividends Paid": "dividends_paid_myr_m",
        "Net Income": "net_income_myr_m",
        "Depreciation And Amortization": "depreciation_amortization_myr_m",
    }
    for attribute in ("quarterly_cashflow", "cashflow"):
        try:
            compact = compact_statement(getattr(stock, attribute), fields, max_periods=4)
        except Exception:  # noqa: BLE001 - yfinance raises provider/version-specific exceptions here.
            continue
        if compact:
            return compact
    return {}


def compact_valuation_ratios(stock: StockPayload) -> StockPayload:
    ratios = {
        "pe": stock.get("pe_ratio"),
        "forward_pe": stock.get("forward_pe"),
        "dividend_yield_pct": stock.get("dividend_yield"),
        "price_to_book": stock.get("price_to_book"),
        "price_to_sales": stock.get("price_to_sales"),
        "ev_to_ebitda": stock.get("enterprise_to_ebitda"),
        "trailing_eps": stock.get("trailing_eps"),
        "book_value_per_share": stock.get("book_value"),
    }
    compact: StockPayload = {key: value for key, value in ratios.items() if value not in (None, "N/A")}
    market_cap = finite_number(stock.get("market_cap"))
    enterprise_value = finite_number(stock.get("enterprise_value"))
    if market_cap is not None:
        compact["market_cap_myr_m"] = round(market_cap / 1_000_000, 2)
    if enterprise_value is not None:
        compact["enterprise_value_myr_m"] = round(enterprise_value / 1_000_000, 2)
    return compact


def build_data_quality_check(
    stock: StockPayload,
    quarterly_summary: CompactStatement,
    balance_sheet: CompactStatement,
    cash_flow: CompactStatement,
    valuation_ratios: StockPayload,
) -> StockPayload:
    coverage = {
        "market_snapshot": bool(stock.get("current_price")),
        "income_statement": bool(quarterly_summary),
        "quarterly_financial_summary": bool(quarterly_summary),
        "balance_sheet": bool(balance_sheet),
        "cash_flow": bool(cash_flow),
        "valuation_ratios": bool(valuation_ratios),
    }
    missing = [name for name, present in coverage.items() if not present]
    return {
        "status": "complete" if not missing else "partial",
        "coverage": coverage,
        "missing": missing,
    }


class YFinanceUtils:
    @staticmethod
    def search_stock_by_name(query: str) -> list[dict[str, str]]:
        if not query or len(query.strip()) < MIN_SEARCH_QUERY_LENGTH:
            return []
        try:
            results = yf.Search(query, max_results=8).quotes
            formatted = []
            for item in results:
                symbol = item.get("symbol", "")
                name = item.get("shortname") or item.get("longname") or symbol
                exchange = item.get("exchange") or item.get("exchDisp") or "N/A"
                if symbol.endswith(".KL") or exchange in {"KLS", "KLSE", "Kuala Lumpur"}:
                    formatted.append(
                        {
                            "symbol": symbol,
                            "name": name,
                            "exchange": exchange,
                            "display": f"{name} ({symbol}) - {exchange}",
                        }
                    )
        except Exception:  # noqa: BLE001 - search backends fail with multiple transport exception types.
            return []
        else:
            return formatted

    @staticmethod
    def get_stock_history(symbol: str, period: str = "1y") -> pd.DataFrame:
        ticker_code = resolve_bursa_ticker(symbol)
        return yf.Ticker(ticker_code).history(period=period)

    @staticmethod
    def get_stock_info(symbol: str) -> StockPayload:
        ticker_code = resolve_bursa_ticker(symbol)
        try:
            stock_info = yf.Ticker(ticker_code).info or {}
        except Exception as exc:  # noqa: BLE001 - yfinance/curl exception classes vary by version.
            return StockInfoResult(
                symbol=ticker_code,
                company_name=symbol,
                source="yfinance",
                status="FAILED",
                confidence="low",
                warnings=["Yahoo Finance fundamentals unavailable."],
                error=str(exc) or type(exc).__name__,
            ).model_dump()

        current_price = (
            stock_info.get("currentPrice") or stock_info.get("regularMarketPrice") or stock_info.get("previousClose")
        )
        dividend_yield = stock_info.get("dividendYield")
        if dividend_yield is not None and dividend_yield < 1:
            dividend_yield = dividend_yield * 100

        warnings = ["Market and fundamentals sourced from yfinance; verify against official filings."]
        if not finite_positive_market_number(current_price):
            warnings.append("Current price is missing or invalid.")

        return StockInfoResult(
            symbol=ticker_code,
            company_name=stock_info.get("longName") or stock_info.get("shortName") or symbol,
            source="yfinance",
            status="SUCCESS",
            confidence="medium",
            warnings=warnings,
            sector=stock_info.get("sector", "N/A"),
            industry=stock_info.get("industry", "N/A"),
            currency=stock_info.get("currency", "MYR"),
            current_price=rounded_positive(current_price, 3),
            pe_ratio=rounded_positive(stock_info.get("trailingPE"), 2),
            forward_pe=rounded_positive(stock_info.get("forwardPE"), 2),
            dividend_yield=rounded_positive(dividend_yield, 2),
            fifty_two_week_high=stock_info.get("fiftyTwoWeekHigh", "N/A"),
            fifty_two_week_low=stock_info.get("fiftyTwoWeekLow", "N/A"),
            market_cap=stock_info.get("marketCap"),
            price_to_book=rounded_positive(stock_info.get("priceToBook"), 2),
            price_to_sales=rounded_positive(stock_info.get("priceToSalesTrailing12Months"), 2),
            enterprise_value=stock_info.get("enterpriseValue"),
            enterprise_to_ebitda=rounded_positive(stock_info.get("enterpriseToEbitda"), 2),
            trailing_eps=rounded_positive(stock_info.get("trailingEps"), 4),
            book_value=rounded_positive(stock_info.get("bookValue"), 4),
            summary=(stock_info.get("longBusinessSummary") or "")[:120],
        ).model_dump()

    @staticmethod
    def get_quarterly_reports(symbol: str) -> StockPayload:
        ticker_code = resolve_bursa_ticker(symbol)
        stock = yf.Ticker(ticker_code)
        company_name = symbol
        try:
            info = stock.info or {}
            company_name = info.get("longName") or info.get("shortName") or symbol
        except Exception:  # noqa: BLE001 - company name is optional; keep quarterly fallback working.
            pass

        try:
            q_financials = stock.quarterly_financials
            if q_financials is not None and not q_financials.empty and len(q_financials.columns) > 0:
                return QuarterlyReportResult(
                    symbol=ticker_code,
                    company_name=company_name,
                    source="yfinance",
                    status="SUCCESS",
                    confidence="medium",
                    warnings=["Quarterly figures are from yfinance; cross-check against official Bursa filings."],
                    quarterly_financials=compact_quarterly_financials(q_financials),
                ).model_dump()
        except Exception as exc:  # noqa: BLE001 - yfinance/curl exception classes vary by version.
            yfinance_error = str(exc) or type(exc).__name__
        else:
            yfinance_error = "YFinance quarterly dataset empty."

        official = cached_official_bursa_filings(ticker_code)
        return QuarterlyReportResult(
            symbol=ticker_code,
            company_name=company_name,
            source="official_search",
            status="PARTIAL" if official.get("sources") else "FAILED",
            confidence=official.get("confidence", "low"),
            warnings=[yfinance_error, *official.get("warnings", [])],
            sources=official.get("sources", []),
            error=(
                None
                if official.get("sources")
                else "No quarterly financial dataset or official filing search result found."
            ),
        ).model_dump()
