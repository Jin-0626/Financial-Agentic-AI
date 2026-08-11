from functools import lru_cache

from research_agent.schemas import OfficialResearchResult, SourceRecord
from research_agent.settings import settings
from research_agent.tickers import resolve_bursa_ticker

try:
    from tavily import TavilyClient
except ImportError:
    TavilyClient = None


def search_with_tavily(query: str, *, max_results: int = 3) -> list[SourceRecord]:
    if not settings.TAVILY_API_KEY or TavilyClient is None:
        return []
    try:
        tavily = TavilyClient(api_key=settings.TAVILY_API_KEY)
        response = tavily.search(query=query, search_depth="basic", max_results=max_results) or {}
    except Exception:  # noqa: BLE001 - Tavily client/network exceptions vary by installed version.
        return []

    records = []
    for item in response.get("results", []):
        url = item.get("url")
        if not url:
            continue
        records.append(
            SourceRecord(
                title=item.get("title") or "Untitled result",
                url=url,
                provider="tavily_search",
                confidence="medium",
                published_date=item.get("published_date"),
                snippet=(item.get("content") or "")[:180],
            )
        )
    return records


def _is_irrelevant_official_result(source: SourceRecord) -> bool:
    text = f"{source.title} {source.url} {source.snippet}".lower()
    if source.url.startswith("/"):
        return True
    irrelevant_terms = ("escherichia", "e. coli", "o157:h7", "foodborne pathogen")
    return any(term in text for term in irrelevant_terms)


def compact_search_items(records: list[SourceRecord], *, max_items: int = 1) -> list[dict[str, str | None]]:
    return [
        {
            "title": record.title[:72],
            "snippet": record.snippet[:110],
            "published_date": record.published_date,
        }
        for record in records[:max_items]
    ]


def build_market_news_context(company_name: str, ticker: str, sector: str = "", industry: str = "") -> dict:
    """Build compact web-research context for factors that can affect a Bursa company."""
    company = company_name or ticker
    industry_context = industry if industry and industry != "N/A" else sector
    queries = {
        "market_news": f"{company} {ticker} Bursa Malaysia latest earnings outlook",
        "macro_news": f"Malaysia consumer spending interest rates ringgit {sector}",
        "micro_industry_news": f"Malaysia {industry_context} outlook regulation competition",
        "competitor_analysis": f"{company} {ticker} competitors Bursa Malaysia {industry_context}",
    }
    max_results_by_category = {
        "market_news": 1,
        "macro_news": 1,
        "micro_industry_news": 1,
        "competitor_analysis": 1,
    }
    return {
        category: {
            "query": query,
            "items": _safe_compact_search(query, max_results=max_results_by_category[category]),
        }
        for category, query in queries.items()
    }


def build_fast_market_context(company_name: str, ticker: str, sector: str = "", industry: str = "") -> dict:
    industry_context = industry if industry and industry != "N/A" else sector
    query = f"{company_name} {ticker} Bursa earnings outlook Malaysia {industry_context} competitors macro"
    items = _safe_compact_search(query, max_results=2, max_items=2)
    return {
        "market_news": {"query": query, "items": items[:1]},
        "macro_news": {"query": query, "items": items[1:2]},
        "micro_industry_news": {"query": query, "items": []},
        "competitor_analysis": {"query": query, "items": []},
    }


def build_missing_quarter_search(company_name: str, ticker: str, known_periods: list[str]) -> dict:
    """Retry official-first web search when market data has fewer than four quarterly periods."""
    missing_count = max(0, 4 - len(known_periods))
    if missing_count == 0:
        return {
            "status": "not_needed",
            "missing_count": 0,
            "known_periods": known_periods[:4],
            "queries": [],
            "sources": [],
            "warnings": [],
        }

    base_code = ticker.replace(".KL", "")
    period_hint = " ".join(known_periods[:3])
    queries = [
        f"site:bursamalaysia.com {base_code} {company_name} quarterly report preceding quarter revenue net income",
        f"{company_name} {ticker} financial results quarter before {period_hint} revenue profit EPS",
        f"{company_name} annual report quarterly results 4Q Bursa Malaysia {base_code}",
    ]
    sources: list[SourceRecord] = []
    for query in queries:
        sources.extend(search_with_tavily(query, max_results=2))

    deduped: list[SourceRecord] = []
    seen_urls: set[str] = set()
    for source in sources:
        if _is_irrelevant_official_result(source) or source.url in seen_urls:
            continue
        seen_urls.add(source.url)
        deduped.append(source)

    compact_sources = [
        {
            "title": source.title[:90],
            "url": source.url,
            "snippet": source.snippet[:220],
            "published_date": source.published_date,
            "confidence": source.confidence,
        }
        for source in deduped[:4]
    ]
    warnings = []
    if not compact_sources:
        warnings.append("Searched for missing fourth quarter but no official/search result was returned.")
    else:
        warnings.append("Search retry returned possible filing evidence; use only explicit figures from snippets.")

    return {
        "status": "SUCCESS" if compact_sources else "FAILED",
        "missing_count": missing_count,
        "known_periods": known_periods[:4],
        "queries": queries,
        "sources": compact_sources,
        "warnings": warnings,
    }


def _safe_compact_search(query: str, *, max_results: int, max_items: int = 1) -> list[dict[str, str | None]]:
    try:
        return compact_search_items(search_with_tavily(query, max_results=max_results), max_items=max_items)
    except Exception:  # noqa: BLE001 - tool fallback should survive search-provider failures.
        return []


@lru_cache(maxsize=128)
def cached_official_bursa_filings(company_name_or_ticker: str) -> dict:
    ticker = resolve_bursa_ticker(company_name_or_ticker)
    base_name = ticker.replace(".KL", "")
    queries = [
        f"site:bursamalaysia.com {base_name} quarterly report annual report announcement",
        f"{company_name_or_ticker} investor relations quarterly results annual report Malaysia",
    ]
    sources: list[SourceRecord] = []
    for query in queries:
        sources.extend(search_with_tavily(query, max_results=2))

    deduped = []
    seen = set()
    for source in sources:
        if _is_irrelevant_official_result(source):
            continue
        if source.url in seen:
            continue
        seen.add(source.url)
        deduped.append(source)

    warnings = []
    if not deduped:
        warnings.append("No official filing links were found; broaden query or check Bursa manually.")

    result = OfficialResearchResult(
        query=company_name_or_ticker,
        status="SUCCESS" if deduped else "FAILED",
        confidence="high" if deduped and all("bursamalaysia.com" in source.url for source in deduped) else "medium",
        warnings=warnings,
        sources=deduped[:2],
    ).model_dump()
    result["sources"] = [
        {
            "title": source.get("title", "")[:72],
            "url": source.get("url", ""),
            "snippet": source.get("snippet", "")[:110],
        }
        for source in result["sources"]
    ]
    return result
