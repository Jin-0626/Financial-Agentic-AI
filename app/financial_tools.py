"""
Institutional Financial Tools for DeepAgent / LangChain / LangGraph.
Wires native market data, portfolio analytics, financial news, and macro indicators.
"""

from __future__ import annotations
import os
import requests
from urllib.parse import urlsplit, urlencode
import json
import logging
import math
from typing import Any, List, Optional, Literal
from datetime import datetime, timezone, timedelta
from orchestrator.freshness import now_utc

from langchain_core.tools import BaseTool, tool
from langchain.tools import ToolRuntime
from .diagnostics import sanitize_error


logger = logging.getLogger(__name__)

_dependency_errors: dict[str, str] = {}

# Map actual script filenames from scripts/ directory
try:
    from scripts.data_sources import yf_data as stock_data
except Exception as e:
    logger.error(
        "Failed to from scripts.data_sources import yf_data as stock_data: %s", sanitize_error(e)
    )
    _dependency_errors["yfinance"] = sanitize_error(e)
    stock_data = None

try:
    from scripts.data_sources import yh_market as yahoo_market_data
except Exception as e:
    logger.error(
        "Failed to from scripts.data_sources import yh_market as yahoo_market_data: %s",
        sanitize_error(e),
    )
    _dependency_errors["yahoo"] = sanitize_error(e)
    yahoo_market_data = None

_fmp_client = None
try:
    from scripts.data_sources import fmp_client

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
            return _to_json(
                {
                    "error": "Supply a ticker symbol; use company_search to resolve a company name",
                    "category": "validation",
                }
            )

        try:
            if sym in ("MOVERS_GAINERS", "GAINERS", "MOVERS_LOSERS", "LOSERS"):
                if yahoo_market_data is None:
                    return _to_json(
                        {
                            "error": "Yahoo dependency unavailable",
                            "category": "dependency",
                            "detail": _dependency_errors.get("yahoo"),
                        }
                    )
                category = (
                    "gainers" if sym in ("MOVERS_GAINERS", "GAINERS") else "losers"
                )
                return _to_json(yahoo_market_data.get_movers(category, count=15))
            if stock_data is None:
                return _to_json(
                    {
                        "error": "Market data dependency unavailable",
                        "category": "dependency",
                        "detail": _dependency_errors.get(
                            "yfinance", "yf_data not loaded"
                        ),
                    }
                )
            if data_type == "quote":
                return _to_json(stock_data.get_quote(sym))
            elif data_type == "history":
                return _to_json(
                    stock_data.get_historical_period(
                        sym, period=period, interval=interval
                    )
                )
            elif data_type == "overview":
                return _to_json(stock_data.get_info(sym))
            elif data_type == "financials":
                return _to_json(stock_data.get_financials(sym, frequency=frequency))
            else:
                return _to_json(
                    {
                        "error": f"Unknown data_type: {data_type}. Use quote, history, overview, or financials."
                    }
                )
        except Exception as e:
            return _to_json(
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
            return _to_json(
                {
                    "status": "error",
                    "category": "validation",
                    "error": "Supply a query of 1 to 200 characters and count from 1 to 10",
                }
            )
        try:
            from app.providers import search_symbols

            matches = search_symbols(query.strip(), count)
            return _to_json(
                {
                    "status": "success" if matches else "empty",
                    "matches": matches,
                    "provider": "Yahoo Finance",
                }
            )
        except Exception as error:
            return _to_json(
                {
                    "status": "error",
                    "category": "provider",
                    "error": sanitize_error(error),
                    "matches": [],
                }
            )

    return company_search


# -----------------------------------------------------------------------------
# Tool 2: Portfolio Holdings, NAV Replay & Risk
# -----------------------------------------------------------------------------


def _make_portfolio_tool() -> BaseTool:
    import asyncio

    @tool
    async def portfolio_analytics(
        action: str, payload_json: str, runtime: ToolRuntime
    ) -> str:
        """Use Rust for weighted-average transaction replay or explicitly labelled fixed-holdings projection.

        replay_transactions accepts Financial transactions (date,symbol,type,quantity,price).
        current_holdings_nav accepts {positions:[{symbol,quantity,average_cost?}],period,benchmark?}.
        Results are calculations, not stored user portfolios. No cash, fees, taxes or FX.
        """
        from app.native_engine import calculate
        from app.portfolio_service import import_trades, Position
        from app import providers

        org = getattr(runtime.context, "org_id", "default-org")
        try:
            if len(payload_json) > 1048576:
                raise ValueError("Portfolio payload exceeds 1 MiB")
            parsed = json.loads(payload_json)
            if action == "replay_transactions":
                trades = import_trades(
                    {"portfolio_name": "Calculation", "transactions": parsed}
                )
                result = await calculate(
                    org,
                    "portfolio_ledger",
                    {"opening_positions": [], "transactions": trades},
                )
                return _to_json(
                    {
                        **result,
                        "method": "Decimal weighted-average ledger; no historical NAV inferred",
                    }
                )
            if action == "current_holdings_nav":
                if not isinstance(parsed, dict) or not isinstance(
                    parsed.get("positions"), list
                ):
                    raise ValueError("Supply positions")
                positions = [
                    Position.model_validate({"average_cost": "0", **p}).model_dump(
                        mode="json"
                    )
                    for p in parsed["positions"]
                ]
                if not 1 <= len(positions) <= 50:
                    raise ValueError("Use between 1 and 50 holdings")
                benchmark = providers.symbol(
                    parsed.get("benchmark", positions[0]["symbol"])
                )
                symbols = list(
                    dict.fromkeys([p["symbol"] for p in positions] + [benchmark])
                )
                quotes = await asyncio.gather(
                    *(asyncio.to_thread(providers.quote, s) for s in symbols)
                )
                currencies = {q["symbol"]: q["currency"] for q in quotes}
                if len(set(currencies.values())) != 1:
                    raise ValueError("Mixed currencies; FX conversion unavailable")
                period = parsed.get("period", "6mo")
                if period not in {"1mo", "3mo", "6mo", "1y", "2y", "5y"}:
                    raise ValueError("Unsupported period")
                series = await asyncio.to_thread(
                    providers.price_series, symbols, currencies, period
                )
                return _to_json(
                    await calculate(
                        org,
                        "portfolio_performance",
                        {
                            "currency": quotes[0]["currency"],
                            "benchmark": benchmark,
                            "method": "projection",
                            "positions": positions,
                            "series": series,
                        },
                    )
                )
            raise ValueError("Use replay_transactions or current_holdings_nav")
        except Exception as error:
            return _to_json(
                {
                    "status": "error",
                    "error": sanitize_error(error),
                    "category": "calculation",
                }
            )

    return portfolio_analytics


# -----------------------------------------------------------------------------
# Tool 3: Financial News & Corporate Disclosures
# -----------------------------------------------------------------------------


def _search_tavily_news(
    query: str, count: int, api_key: str, days: int | None = 7
) -> list[dict[str, Any]]:
    """Search article evidence; do not use a generated answer as a news source."""
    response = requests.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "query": query,
            "topic": "news",
            "search_depth": "basic",
            "max_results": count,
            "include_answer": False,
            "include_raw_content": False,
            **(
                {
                    "start_date": (now_utc() - timedelta(days=days)).date().isoformat(),
                    "end_date": now_utc().date().isoformat(),
                }
                if days is not None
                else {}
            ),
        },
        timeout=30,
    )
    try:
        response.raise_for_status()
        payload = response.json()
    finally:
        response.close()
    if (
        not isinstance(payload, dict)
        or payload.get("error")
        or not isinstance(payload.get("results"), list)
    ):
        raise ValueError("Tavily returned an invalid news response")
    articles = []
    for item in payload["results"][:count]:
        if not isinstance(item, dict):
            raise ValueError("Tavily returned an invalid article")
        title, url = item.get("title"), item.get("url")
        if not isinstance(title, str) or not title.strip() or not isinstance(url, str):
            raise ValueError("Tavily article is missing a title or source URL")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Tavily article has an invalid source URL")
        articles.append(
            {
                "title": title,
                "summary": item.get("content") or "",
                "pub_date": item.get("published_date"),
                "provider": parsed.hostname,
                "search_provider": "Tavily",
                "url": url,
            }
        )
    return articles


def _public_market_news(
    count: int, query: str | None = None, days: int | None = 7
) -> list[dict[str, Any]]:
    """Bounded public market-news search feed; no key or invented publication dates."""
    from xml.etree import ElementTree
    from email.utils import parsedate_to_datetime

    search = query or "financial markets"
    if days is not None:
        search += f" when:{days}d"
    if len(search) > 1000:
        raise ValueError("News query exceeds length limit")
    feed_url = "https://news.google.com/rss/search?" + urlencode(
        {"q": search, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    )
    response = requests.get(feed_url, timeout=(5, 10), stream=True)
    try:
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_content(chunk_size=16384):
            body.extend(chunk)
            if len(body) > 1024 * 1024:
                raise ValueError("Market news feed exceeds size limit")
        if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
            raise ValueError("Unsupported market news feed structure")
        root = ElementTree.fromstring(body)
    finally:
        response.close()
    articles = []
    seen = set()
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        parsed = urlsplit(url)
        if (
            not title
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or url in seen
        ):
            continue
        date = None
        raw_date = item.findtext("pubDate")
        if raw_date:
            try:
                date = parsedate_to_datetime(raw_date).isoformat()
            except (ValueError, TypeError, OverflowError):
                pass
        seen.add(url)
        articles.append(
            {
                "title": title,
                "summary": item.findtext("description") or "",
                "pub_date": date,
                "provider": item.findtext("source") or "Google News",
                "search_provider": "Google News RSS",
                "url": url,
            }
        )
        if len(articles) == count:
            break
    if not articles and root.tag.lower() != "rss":
        raise ValueError("Invalid market news feed")
    return articles


def _recent_news(articles, days):
    current = now_utc()
    result = []
    for article in articles:
        item = dict(article)
        raw = next(
            (
                item.get(k)
                for k in (
                    "pub_date",
                    "publishedDate",
                    "published_date",
                    "pubDate",
                    "providerPublishTime",
                )
                if item.get(k) is not None
            ),
            None,
        )
        published = None
        try:
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                published = datetime.fromtimestamp(raw, timezone.utc)
            elif isinstance(raw, str):
                try:
                    published = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except ValueError:
                    from email.utils import parsedate_to_datetime
                    published = parsedate_to_datetime(raw)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError, OverflowError, OSError):
            pass
        if (
            days is not None
            and published is not None
            and (
                published < current - timedelta(days=days)
                or published > current + timedelta(minutes=5)
            )
        ):
            continue
        item["publication_status"] = "dated" if published is not None else "unknown"
        item["retrieved_at"] = current.isoformat()
        result.append((published, item))
    result.sort(
        key=lambda pair: pair[0] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return [item for _, item in result]


def _make_news_tool() -> BaseTool:
    @tool
    def financial_news(
        symbol: Optional[str] = None,
        count: int = 10,
        query: Optional[str] = None,
        days: Optional[int] = 7,
    ) -> str:
        """
        Search financial news with Tavily, falling back to FMP, Yahoo and a public RSS search.
        Latest news defaults to the last 7 days; publication dates are checked across every provider.
        Use days=None only for historical research. Undated articles are labelled unknown, not verified current.
        Cite article URLs and dates; search snippets are not audited financial statements.

        Args:
            symbol: Ticker symbol (e.g. 'AAPL', 'TSLA'). If omitted, general market news is returned.
            count: Number of news items to retrieve, from 1 to 20 (default: 10).
            query: Optional search text using a verified company name and relevant event.
                   The symbol is included when supplied; do not invent a company identity.

        Returns:
            JSON string with headlines, publishers, timestamps, and summaries.
        """
        if days is not None and (isinstance(days, bool) or not 1 <= days <= 3650):
            return _to_json(
                {
                    "status": "error",
                    "category": "validation",
                    "error": "days must be 1 to 3650, or null for historical research",
                    "articles": [],
                }
            )
        if not 1 <= count <= 20:
            return _to_json(
                {
                    "status": "error",
                    "category": "validation",
                    "error": "count must be between 1 and 20",
                    "articles": [],
                }
            )
        symbol = symbol.strip().upper() if symbol and symbol.strip() else None
        query = query.strip() if query and query.strip() else None
        attempts = []
        tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
        if tavily_key:
            search_query = " ".join(
                part
                for part in [symbol, query or "latest financial business news"]
                if part
            )
            try:
                articles = _recent_news(
                    _search_tavily_news(search_query, count, tavily_key, days), days
                )
                if articles:
                    return _to_json(articles)
                attempts.append({"provider": "Tavily", "status": "empty"})
            except Exception as e:
                attempts.append(
                    {
                        "provider": "Tavily",
                        "status": "error",
                        "error": sanitize_error(e, secrets=(tavily_key,)),
                    }
                )

        def success(articles):
            # Keep failed primary searches visible when a fallback supplies evidence.
            if any(attempt["status"] == "error" for attempt in attempts):
                return _to_json(
                    {
                        "articles": articles,
                        "status": "partial_success",
                        "attempts": attempts,
                    }
                )
            return _to_json(articles)

        # Ticker feeds do not implement free-text search. Never substitute unrelated
        # ticker/latest headlines for a user's explicit research query.
        if not query and _fmp_client and getattr(_fmp_client, "api_key", None):
            try:
                params = {"limit": count}
                if symbol:
                    params["symbols"] = symbol.upper()
                endpoint = "news/stock" if symbol else "news/stock-latest"
                news = _fmp_client._request(endpoint, params, version="stable")
                if not isinstance(news, list):
                    raise ValueError("FMP returned a non-list news response")
                news = _recent_news(news, days)
                if news:
                    return success(news)
                attempts.append({"provider": "FMP", "status": "empty"})
            except Exception as e:
                attempts.append(
                    {"provider": "FMP", "status": "error", "error": sanitize_error(e)}
                )
        if symbol and not query:
            try:
                # News does not depend on whether the analytics module imported successfully.
                import yfinance as yf

                raw_news = yf.Ticker(symbol.upper()).news
                if not isinstance(raw_news, list):
                    raise ValueError("Yahoo returned a non-list news response")
                cleaned = []
                for item in raw_news[:count]:
                    content = item.get("content", item)
                    cleaned.append(
                        {
                            "title": content.get("title", ""),
                            "summary": content.get("summary", ""),
                            "pub_date": content.get("pubDate")
                            or content.get("publishedDate")
                            or content.get("providerPublishTime"),
                            "provider": content.get("publisher")
                            or (
                                (content.get("provider") or {}).get("displayName", "")
                                if isinstance(content.get("provider"), dict)
                                else content.get("provider") or "Yahoo Finance"
                            ),
                            "url": (content.get("canonicalUrl") or {}).get(
                                "url", content.get("link", "")
                            ),
                        }
                    )
                cleaned = _recent_news(cleaned, days)
                if cleaned:
                    return success(cleaned)
                attempts.append({"provider": "Yahoo", "status": "empty"})
            except Exception as e:
                attempts.append(
                    {"provider": "Yahoo", "status": "error", "error": sanitize_error(e)}
                )
        try:
            articles = _public_market_news(
                count,
                query=" ".join(part for part in [symbol, query] if part) or None,
                days=days,
            )
            articles = _recent_news(articles, days)
            if articles:
                return success(articles)
            attempts.append({"provider": "Google News RSS", "status": "empty"})
        except Exception as e:
            attempts.append(
                {
                    "provider": "Google News RSS",
                    "status": "error",
                    "error": sanitize_error(e),
                }
            )
        if not tavily_key:
            attempts.append(
                {
                    "provider": "Tavily",
                    "status": "unavailable",
                    "detail": "TAVILY_API_KEY is not configured",
                }
            )
        status = (
            "error"
            if any(a["status"] == "error" for a in attempts)
            else "empty"
            if any(a["status"] == "empty" for a in attempts)
            else "unavailable"
        )
        result = {"articles": [], "status": status, "attempts": attempts}
        if status != "empty":
            result["error"] = (
                "News retrieval failed"
                if attempts
                else "No news provider configured for this request"
            )
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
    fmp_key = getattr(_fmp_client, "api_key", None)

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

    if _fmp_client and fmp_key:
        try:
            rows = _fmp_client.get_treasury_rates()
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
        if stock_data is None:
            record_error(
                "Yahoo",
                _dependency_errors.get(
                    "yfinance", "Market data dependency unavailable"
                ),
            )
        else:
            try:
                quotes = stock_data.get_batch_quotes(list(expected_symbols))
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
            return _to_json(
                {"error": f"Unknown indicator: {indicator}", "category": "input"}
            )
        if ind == "treasury":
            return _to_json(_treasury_result())
        provider_errors = []
        # 1. Market Index Summary
        if ind in ("summary", "indices", "market") and yahoo_market_data:
            try:
                return _to_json(yahoo_market_data.get_market_summary(region="US"))
            except Exception as e:
                return _to_json(
                    {
                        "error": sanitize_error(e),
                        "provider": "Yahoo",
                        "category": "provider",
                    }
                )

        # 2. Economic releases via FMP
        if _fmp_client and _fmp_client.api_key:
            try:
                if ind == "calendar":
                    return _to_json(_fmp_client.get_economic_calendar())
            except Exception as e:
                provider_errors.append({"provider": "FMP", "error": sanitize_error(e)})

        return _to_json(
            {
                "error": f"Indicator '{indicator}' unavailable",
                "provider_errors": provider_errors,
                "note": "Calendar requires configured FMP; sandbox status does not determine provider availability.",
            }
        )

    return economics_data


# -----------------------------------------------------------------------------
# Tool Aggregator
# -----------------------------------------------------------------------------


def build_financial_tools() -> List[BaseTool]:
    """Build and return all active institutional finance tools for the agent."""
    return [
        _make_company_search_tool(),
        _make_market_data_tool(),
        _make_portfolio_tool(),
        _make_news_tool(),
        _make_economics_tool(),
    ]
