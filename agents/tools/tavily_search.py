import json
import logging
import os

from tavily import AsyncTavilyClient

logger = logging.getLogger(__name__)

tavily_api_key = os.getenv("TAVILY_API_KEY")
tavily_client = AsyncTavilyClient(api_key=tavily_api_key) if tavily_api_key else None


def _clip(text: object, limit: int = 700) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


async def search_bursa_intelligence(stock_code: str, company_name: str) -> str:
    """Fetch recent news, corporate announcements, and retail chatter via Tavily."""
    if not tavily_client:
        return f"Tavily API key not configured. Skipping live web search for {company_name} ({stock_code})."

    query = f"{company_name} {stock_code} Bursa Malaysia financial news quarterly results"
    try:
        # Tavily search call
        response = await tavily_client.search(
            query=query,
            search_depth="basic",
            max_results=3,
        )
        results = response.get("results", [])
        if not results:
            return f"No recent online intelligence found for {company_name}."

        formatted = {
            "company_name": company_name,
            "stock_code": stock_code,
            "query": query,
            "results": [],
        }
        for r in results:
            formatted["results"].append(
                {
                    "title": _clip(r.get("title"), 180),
                    "url": r.get("url"),
                    "published_date": r.get("published_date") or r.get("date"),
                    "snippet": _clip(r.get("content"), 700),
                }
            )
        return json.dumps(formatted, ensure_ascii=False)

    except Exception as exc:  # noqa: BLE001 - external Tavily client exceptions vary by transport.
        logger.error("Tavily search error: %s", exc)
        return f"Error fetching live news intelligence: {exc}"
