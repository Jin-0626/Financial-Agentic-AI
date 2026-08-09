import os
import math
from typing import Dict, List, Optional

import yfinance as yf
from pandas import DataFrame
from langchain_core.tools import tool
from langsmith import traceable
from observability import finite_positive_number
from schemas import BursaTickerRequest, DCFValuationRequest, DCFValuationResult, QuarterlyReportResult, StockInfoResult
from settings import settings

try:
    from tavily import TavilyClient
except ImportError:
    TavilyClient = None

@traceable(name="Yahoo Finance ticker resolution", run_type="tool")
def resolve_bursa_ticker(symbol_or_name: str) -> str:
    """Resolves company names, 4-digit KLSE stock codes, or ticker strings to valid .KL symbols."""
    clean_input = BursaTickerRequest(query=symbol_or_name).query.upper()
    if clean_input.endswith(".KL"):
        return clean_input
    if clean_input.isdigit() and len(clean_input) <= 4:
        return f"{clean_input.zfill(4)}.KL"

    try:
        search_results = yf.Search(clean_input, max_results=8).quotes
        for quote in search_results:
            symbol = quote.get("symbol", "")
            exchange = quote.get("exchange", "")
            if symbol.endswith(".KL") or exchange in ["KLS", "KLSE", "SES"]:
                return symbol if symbol.endswith(".KL") else f"{symbol}.KL"
    except Exception as exc:
        raise ValueError(f"Unable to resolve Bursa ticker for {symbol_or_name!r}: {exc}") from exc

    return f"{clean_input}.KL"


class YFinanceUtils:

    @staticmethod
    def search_stock_by_name(query: str) -> List[Dict[str, str]]:
        """Searches Yahoo Finance for matching stock names and returns candidate tickers."""
        if not query or len(query.strip()) < 2:
            return []
        try:
            results = yf.Search(query, max_results=8).quotes
            formatted_results = []
            for item in results:
                symbol = item.get("symbol", "")
                name = item.get("shortname") or item.get("longname") or symbol
                exchange = item.get("exchange") or item.get("exchDisp") or "N/A"
                formatted_results.append({
                    "symbol": symbol,
                    "name": name,
                    "exchange": exchange,
                    "display": f"{name} ({symbol}) - {exchange}"
                })
            return formatted_results
        except Exception:
            return []

    @staticmethod
    def get_stock_history(symbol: str, period: str = "1y") -> DataFrame:
        """Fetches historical price data for charting."""
        ticker_code = resolve_bursa_ticker(symbol)
        stock = yf.Ticker(ticker_code)
        df = stock.history(period=period)
        return df

    @staticmethod
    @traceable(name="Yahoo Finance fundamentals", run_type="tool")
    def get_stock_info(symbol: str) -> dict:
        """Fetches latest stock information with defensive defaults for missing values."""
        try:
            ticker_code = resolve_bursa_ticker(symbol)
            stock = yf.Ticker(ticker_code)
            stock_info = stock.info or {}
        except Exception as exc:
            return StockInfoResult(
                symbol=symbol,
                company_name=symbol,
                source="yfinance",
                status="FAILED",
                error=f"Yahoo Finance fundamentals unavailable: {exc}",
                error_type=type(exc).__name__,
            ).model_dump()
        
        current_price = (
            stock_info.get("currentPrice")
            or stock_info.get("regularMarketPrice")
            or stock_info.get("previousClose")
        )
        safe_price = finite_positive_number(current_price)
        trailing_pe = stock_info.get("trailingPE")
        div_yield_pct = stock_info.get("dividendYield")


        return StockInfoResult(
            symbol=ticker_code,
            source="yfinance",
            status="SUCCESS",
            company_name=stock_info.get("longName") or stock_info.get("shortName") or symbol,
            sector=stock_info.get("sector", "N/A"),
            industry=stock_info.get("industry", "N/A"),
            currency=stock_info.get("currency", "MYR"),
            current_price=round(safe_price, 3) if safe_price is not None else None,
            pe_ratio=round(float(trailing_pe), 2) if finite_positive_number(trailing_pe) else None,
            forward_pe=round(float(stock_info.get("forwardPE")), 2) if finite_positive_number(stock_info.get("forwardPE")) else None,
            dividend_yield=(round(float(div_yield_pct), 2)
              if div_yield_pct is not None
              else None),
            fifty_two_week_high=stock_info.get("fiftyTwoWeekHigh", "N/A"),
            fifty_two_week_low=stock_info.get("fiftyTwoWeekLow", "N/A"),
            market_cap=stock_info.get("marketCap"),
            summary=stock_info.get("longBusinessSummary", "")[:300] + "..."
        ).model_dump()
    @staticmethod
    @traceable(name="Yahoo Finance quarterly financials", run_type="tool")
    def get_quarterly_reports(symbol: str) -> dict:
        """Fetches quarterly financial statements. Falls back to Tavily Web Search if yfinance data is missing."""
        ticker_code = resolve_bursa_ticker(symbol)
        stock = yf.Ticker(ticker_code)
        
        company_name = symbol
        try:
            stock_info = stock.info or {}
            company_name = stock_info.get("longName") or stock_info.get("shortName") or symbol
        except Exception:
            pass

        # Tier 1: 尝试通过 yfinance 抓取季报
        fallback_reason = "YFinance quarterly dataset empty"
        try:
            q_financials = stock.quarterly_financials
            if q_financials is not None and not q_financials.empty and len(q_financials.columns) > 0:
                # 转换 DataFrame 为字典格式（截取前 4 个季度）
                recent_quarters = q_financials.iloc[:, :4].to_dict()
                data_quality_review = YFinanceUtils._double_check_quarterly_financials(
                    stock,
                    recent_quarters,
                    company_name=company_name,
                    ticker_code=ticker_code,
                )
                return QuarterlyReportResult(
                    symbol=ticker_code,
                    company_name=company_name,
                    source="yfinance",
                    status="SUCCESS",
                    fallback_used=False,
                    quarterly_financials=recent_quarters,
                    data_quality_review=data_quality_review
                ).model_dump()
        except Exception as exc:
            fallback_reason = f"YFinance quarterly financials failed: {exc}"

        # Tier 2: Fallback to Tavily Web Search if yfinance fails
        tavily_api_key = os.getenv("TAVILY_API_KEY") or settings.TAVILY_API_KEY
        if not tavily_api_key:
            return QuarterlyReportResult(
                symbol=ticker_code,
                company_name=company_name,
                source="none",
                status="FAILED",
                fallback_used=True,
                fallback_provider="tavily_search",
                fallback_reason=fallback_reason,
                error=f"{fallback_reason}; Tavily fallback could not execute because TAVILY_API_KEY is not set."
            ).model_dump()
        if TavilyClient is None:
            return QuarterlyReportResult(
                symbol=ticker_code,
                company_name=company_name,
                source="none",
                status="FAILED",
                fallback_used=True,
                fallback_provider="tavily_search",
                fallback_reason=fallback_reason,
                error=f"{fallback_reason}; Tavily fallback could not execute because the tavily package is not installed.",
            ).model_dump()

        try:
            tavily = TavilyClient(api_key=tavily_api_key)
            query = f"{company_name} ({ticker_code}) quarterly financial results net profit revenue Bursa Malaysia"
            search_res = tavily.search(query=query, search_depth="advanced", max_results=3) or {}
            
            results = [
                {
                    "title": r.get("title"),
                    "url": r.get("url"),
                    "content": r.get("content"),
                    "published_date": r.get("published_date", "N/A")
                } for r in search_res.get("results", [])
            ]

            return QuarterlyReportResult(
                symbol=ticker_code,
                company_name=company_name,
                source="tavily_search",
                status="FALLBACK_SUCCESS",
                fallback_used=True,
                fallback_provider="tavily_search",
                fallback_reason=fallback_reason,
                search_query=query,
                extracted_reports=results
            ).model_dump()
        except Exception as e:
            return QuarterlyReportResult(
                symbol=ticker_code,
                company_name=company_name,
                source="tavily_search",
                status="FAILED",
                fallback_used=True,
                fallback_provider="tavily_search",
                fallback_reason=fallback_reason,
                error=f"Failed to fetch via both yfinance and Tavily: {str(e)}"
            ).model_dump()

    @staticmethod
    def _double_check_quarterly_financials(
        stock,
        primary_quarters: dict,
        *,
        company_name: str,
        ticker_code: str,
    ) -> dict:
        checks = []
        warnings = []
        mismatches = []
        tavily_evidence = YFinanceUtils._search_tavily_quarterly_evidence(company_name, ticker_code)
        for period, rows in list(primary_quarters.items())[:4]:
            YFinanceUtils._append_eps_sanity_warnings(period, rows, warnings)
        try:
            secondary = getattr(stock, "quarterly_income_stmt", None)
            if secondary is None or secondary.empty or len(secondary.columns) == 0:
                return {
                    "status": "UNCHECKED",
                    "primary_source": "quarterly_financials",
                    "secondary_source": "quarterly_income_stmt",
                    "checks": checks,
                    "warnings": warnings + ["Yahoo Finance alternate quarterly income statement unavailable."],
                    "mismatches": mismatches,
                    "tavily_evidence": tavily_evidence,
                }
            secondary_quarters = secondary.iloc[:, :4].to_dict()
        except Exception as exc:
            return {
                "status": "UNCHECKED",
                "primary_source": "quarterly_financials",
                "secondary_source": "quarterly_income_stmt",
                "checks": checks,
                "warnings": warnings + [f"Yahoo Finance alternate quarterly income statement failed: {exc}"],
                "mismatches": mismatches,
                "tavily_evidence": tavily_evidence,
            }

        fields = (
            "Total Revenue",
            "Operating Revenue",
            "EBITDA",
            "Operating Income",
            "Pretax Income",
            "Net Income",
            "Diluted EPS",
            "Basic EPS",
        )
        primary_items = list(primary_quarters.items())[:4]
        secondary_items = list(secondary_quarters.items())[:4]
        for (primary_period, primary_rows), (secondary_period, secondary_rows) in zip(primary_items, secondary_items):
            for field in fields:
                primary_value = primary_rows.get(field) if isinstance(primary_rows, dict) else None
                secondary_value = secondary_rows.get(field) if isinstance(secondary_rows, dict) else None
                if primary_value is None or secondary_value is None:
                    continue
                checks.append(f"{str(primary_period)[:10]} {field}")
                primary_number = finite_positive_number(primary_value) or 0
                secondary_number = finite_positive_number(secondary_value) or 0
                tolerance = max(abs(primary_number) * 0.01, 1.0)
                if abs(primary_number - secondary_number) > tolerance:
                    mismatches.append(
                        {
                            "period": str(primary_period)[:10],
                            "field": field,
                            "primary_value": primary_value,
                            "secondary_value": secondary_value,
                            "secondary_period": str(secondary_period)[:10],
                        }
                    )

        status = "MISMATCH" if mismatches else ("WARNING" if warnings else "VERIFIED")
        if not checks:
            status = "UNCHECKED"
            warnings.append("No overlapping numeric fields were available for secondary quarterly validation.")
        return {
            "status": status,
            "primary_source": "quarterly_financials",
            "secondary_source": "quarterly_income_stmt",
            "checks": checks[:24],
            "warnings": warnings,
            "mismatches": mismatches[:10],
            "tavily_evidence": tavily_evidence,
        }

    @staticmethod
    def _search_tavily_quarterly_evidence(company_name: str, ticker_code: str) -> dict:
        tavily_api_key = os.getenv("TAVILY_API_KEY") or settings.TAVILY_API_KEY
        query = f"{company_name} {ticker_code} quarterly financial results revenue net profit EPS Bursa"
        if not tavily_api_key:
            return {
                "status": "SKIPPED",
                "provider": "tavily_search",
                "query": query,
                "reason": "TAVILY_API_KEY is not set.",
                "results": [],
            }
        if TavilyClient is None:
            return {
                "status": "SKIPPED",
                "provider": "tavily_search",
                "query": query,
                "reason": "tavily package is not installed.",
                "results": [],
            }
        try:
            tavily = TavilyClient(api_key=tavily_api_key)
            search_res = tavily.search(query=query, search_depth="advanced", max_results=5) or {}
            results = [
                {
                    "title": r.get("title"),
                    "url": r.get("url"),
                    "content": (r.get("content") or "")[:500],
                    "published_date": r.get("published_date", "N/A"),
                }
                for r in search_res.get("results", [])
            ]
            return {
                "status": "SUCCESS" if results else "EMPTY",
                "provider": "tavily_search",
                "query": query,
                "results": results,
            }
        except Exception as exc:
            return {
                "status": "FAILED",
                "provider": "tavily_search",
                "query": query,
                "reason": str(exc) or type(exc).__name__,
                "results": [],
            }

    @staticmethod
    def _append_eps_sanity_warnings(period, rows: dict, warnings: list) -> None:
        if not isinstance(rows, dict):
            return
        net_income = finite_positive_number(rows.get("Net Income") or rows.get("Net Income Common Stockholders"))
        shares = finite_positive_number(rows.get("Diluted Average Shares") or rows.get("Basic Average Shares"))
        eps = rows.get("Diluted EPS")
        if eps is None:
            eps = rows.get("Basic EPS")
        eps_number = YFinanceUtils._finite_number(eps)
        period_label = str(period)[:10]
        if net_income and eps_number == 0:
            warnings.append(
                f"{period_label}: EPS is 0.0 while net income is positive; preserve EPS precision and verify source."
            )
        if net_income and shares and eps_number is not None:
            calculated_eps = net_income / shares
            tolerance = max(abs(calculated_eps) * 0.05, 0.0005)
            if abs(eps_number - calculated_eps) > tolerance:
                warnings.append(
                    f"{period_label}: reported EPS {eps_number:.4f} differs from net income/shares {calculated_eps:.4f}."
                )

    @staticmethod
    def _finite_number(value) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

# =====================================================================
# LANGCHAIN TOOL WRAPPERS
# =====================================================================

@tool
def fetch_bursa_stock_data(ticker_code_or_name: str) -> dict:
    """Fetches fundamental metrics and business summaries for Bursa Malaysia listed stocks."""
    return YFinanceUtils.get_stock_info(ticker_code_or_name)

@tool
def search_bursa_stock(query: str) -> List[dict]:
    """Searches for Bursa stock codes by company name or keyword using Yahoo Finance."""
    return YFinanceUtils.search_stock_by_name(query)

@tool
def fetch_bursa_quarterly_reports(ticker_code_or_name: str) -> dict:
    """Fetches quarterly financial statements (revenue, net profit) for Bursa stocks. 
    Automatically triggers Tavily Web Search if yfinance quarterly data is missing."""
    return YFinanceUtils.get_quarterly_reports(ticker_code_or_name)

@tool(args_schema=DCFValuationRequest)
@traceable(name="DCF calculation", run_type="tool")
def calculate_dcf_val(
    current_price: Optional[float],
    pe_ratio: Optional[float] = None,
    growth_rate: float = 0.08,
    wacc: float = 0.08,
    terminal_growth_rate: float = 0.02,
) -> dict:
    """Computes a 5-year discounted earnings intrinsic fair value estimate in MYR."""
    request = DCFValuationRequest(
        current_price=current_price,
        pe_ratio=pe_ratio,
        growth_rate=growth_rate,
        wacc=wacc,
        terminal_growth_rate=terminal_growth_rate,
    )
    current_price = request.current_price
    pe_ratio = request.pe_ratio
    growth_rate = request.growth_rate
    wacc = request.wacc
    terminal_growth_rate = request.terminal_growth_rate
    price = finite_positive_number(current_price)
    if price is None:
        return DCFValuationResult(
            status="FAILED",
            error="DCF cannot be calculated because current price is unavailable or invalid.",
        ).model_dump()

    pe = finite_positive_number(pe_ratio)
    pe_substituted = False
    if pe is None:
        pe = 15.0
        pe_substituted = True
        
    eps = price / pe
    projected_eps = [round(eps * ((1 + growth_rate) ** year), 4) for year in range(1, 6)]
    projected_fcff_per_share = [round(value * 0.75, 4) for value in projected_eps]
    proj_eps_5yr = projected_eps[-1]
    terminal_pe = 14.0
    fair_value = (proj_eps_5yr * terminal_pe) / ((1 + wacc) ** 5)
    
    return DCFValuationResult(
        status="SUCCESS",
        pe_ratio_used=pe,
        growth_rate=growth_rate,
        wacc=wacc,
        discount_rate=wacc,
        terminal_growth_rate=terminal_growth_rate,
        terminal_pe=terminal_pe,
        eps_input=round(eps, 4),
        projected_eps=projected_eps,
        projected_fcff_per_share=projected_fcff_per_share,
        pe_ratio_substituted=pe_substituted,
        pe_ratio_substitution_reason="FBM KLCI market baseline" if pe_substituted else None,
        estimated_fair_value_myr=round(fair_value, 2),
        upside_downside_pct=round(((fair_value - price) / price) * 100, 2)
    ).model_dump()
