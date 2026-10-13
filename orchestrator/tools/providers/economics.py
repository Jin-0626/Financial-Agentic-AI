from __future__ import annotations
import logging, math
from typing import Any
from langchain_core.tools import BaseTool, tool
from app.infrastructure.diagnostics import sanitize_error
from . import common
logger = logging.getLogger(__name__)

def _treasury_result() -> dict[str, Any]:
    """Use one schema for direct rates, proxies, partial coverage, and complete failure."""
    direct_rates: list[dict[str, Any]] = []
    proxies: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    expected_symbols = ("^IRX", "^FVX", "^TNX", "^TYX")
    maturity_fields = (
        "month1",
        "month2",
        "month3",
        "month6",
        "year1",
        "year2",
        "year3",
        "year5",
        "year7",
        "year10",
        "year20",
        "year30",
    )
    fmp_key = getattr(common._fmp_client, "api_key", None)

    def record_error(provider: str, message: object, symbol: str | None = None):
        error = {
            "provider": provider,
            "error": sanitize_error(
                message, secrets=(fmp_key,) if isinstance(fmp_key, str) else ()
            ),
        }
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

    if common._fmp_client and fmp_key:
        try:
            rows = common._fmp_client.get_treasury_rates()
            if not isinstance(rows, list) or not rows:
                record_error(
                    "FMP",
                    rows.get("error", "Invalid treasury response")
                    if isinstance(rows, dict)
                    else "No treasury rates returned",
                )
            else:
                for row in rows:
                    if not isinstance(row, dict) or row.get("error"):
                        record_error(
                            "FMP",
                            row.get("error", "Invalid treasury row")
                            if isinstance(row, dict)
                            else "Invalid treasury row",
                        )
                        continue
                    valid = {
                        field: value
                        for field, value in row.items()
                        if field in maturity_fields and finite_number(value)
                    }
                    if not valid or not isinstance(row.get("date"), str):
                        record_error(
                            "FMP", "Treasury row lacks dated finite yield values"
                        )
                        continue
                    invalid = [
                        field
                        for field in maturity_fields
                        if field in row and not finite_number(row[field])
                    ]
                    if invalid:
                        record_error(
                            "FMP", "Unavailable yield fields: " + ", ".join(invalid)
                        )
                    direct_rates.append({"date": row["date"], **valid})
        except Exception as e:
            record_error("FMP", e)

    # Use proxies if direct retrieval failed or returned incomplete data; retain valid direct rows.
    if not direct_rates or errors:
        if common.stock_data is None:
            record_error(
                "Yahoo",
                common._dependency_errors.get(
                    "yfinance", "Market data dependency unavailable"
                ),
            )
        else:
            try:
                quotes = common.stock_data.get_batch_quotes(list(expected_symbols))
                if not isinstance(quotes, list):
                    record_error(
                        "Yahoo",
                        quotes.get("error", "Invalid quote response")
                        if isinstance(quotes, dict)
                        else "Invalid quote response",
                    )
                    quotes = []
                seen = set()
                for quote in quotes:
                    if not isinstance(quote, dict):
                        record_error("Yahoo", "Invalid quote row")
                        continue
                    symbol = quote.get("symbol")
                    if quote.get("error"):
                        record_error(
                            "Yahoo",
                            quote["error"],
                            symbol if isinstance(symbol, str) else None,
                        )
                    elif (
                        symbol not in expected_symbols
                        or symbol in seen
                        or not finite_number(quote.get("price"))
                    ):
                        record_error(
                            "Yahoo",
                            "Quote lacks a unique expected symbol and finite yield value",
                            symbol if isinstance(symbol, str) else None,
                        )
                    else:
                        proxies.append(quote)
                        seen.add(symbol)
                for symbol in expected_symbols:
                    if symbol not in seen:
                        record_error("Yahoo", "Treasury proxy unavailable", symbol)
            except Exception as e:
                record_error("Yahoo", e)

    has_data = bool(direct_rates or proxies)
    status = (
        "partial_success" if has_data and errors else "success" if has_data else "error"
    )
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
            return common._to_json(
                {"error": f"Unknown indicator: {indicator}", "category": "input"}
            )
        if ind == "treasury":
            return common._to_json(_treasury_result())
        provider_errors = []
        # 1. Market Index Summary
        if ind in ("summary", "indices", "market") and common.yahoo_market_data:
            try:
                return common._to_json(common.yahoo_market_data.get_market_summary(region="US"))
            except Exception as e:
                return common._to_json(
                    {
                        "error": sanitize_error(e),
                        "provider": "Yahoo",
                        "category": "provider",
                    }
                )

        # 2. Economic releases via FMP
        if common._fmp_client and common._fmp_client.api_key:
            try:
                if ind == "calendar":
                    return common._to_json(common._fmp_client.get_economic_calendar())
            except Exception as e:
                provider_errors.append({"provider": "FMP", "error": sanitize_error(e)})

        return common._to_json(
            {
                "error": f"Indicator '{indicator}' unavailable",
                "provider_errors": provider_errors,
                "note": "Calendar requires configured FMP; sandbox status does not determine provider availability.",
            }
        )

    return economics_data
