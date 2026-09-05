import json
import logging
import os

import httpx
from tavily import AsyncTavilyClient

from agents.config import default_config

logger = logging.getLogger(__name__)

tavily_client: AsyncTavilyClient | None = None


def _get_tavily_client() -> AsyncTavilyClient | None:
    """Build Tavily lazily so tests/env changes are respected and dead proxies are ignored."""
    global tavily_client
    if tavily_client is not None:
        return tavily_client

    tavily_api_key = os.getenv("TAVILY_API_KEY") or str(
        default_config.get("tavily_api_key", "")
    )
    if not tavily_api_key:
        return None

    tavily_client = AsyncTavilyClient(
        api_key=tavily_api_key,
        client=httpx.AsyncClient(timeout=20.0, trust_env=False),
    )
    return tavily_client


def _clip(text: object, limit: int = 700) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


async def search_bursa_intelligence(stock_code: str, company_name: str) -> str:
    """Fetch recent news, corporate announcements, and retail chatter via Tavily."""
    client = _get_tavily_client()
    if not client:
        return json.dumps(
            {
                "ok": False,
                "source": "tavily",
                "company_name": company_name,
                "stock_code": stock_code,
                "error_type": "missing_api_key",
                "message": "Tavily API key not configured.",
                "results": [],
            },
            ensure_ascii=False,
        )

    query = (
        f"{company_name} {stock_code} Bursa Malaysia financial news quarterly results"
    )
    try:
        response = await client.search(
            query=query,
            search_depth="basic",
            max_results=3,
        )
        results = response.get("results", [])
        if not results:
            return json.dumps(
                {
                    "ok": False,
                    "source": "tavily",
                    "company_name": company_name,
                    "stock_code": stock_code,
                    "query": query,
                    "error_type": "no_results",
                    "message": f"No recent online intelligence found for {company_name}.",
                    "results": [],
                },
                ensure_ascii=False,
            )

        formatted = {
            "ok": True,
            "source": "tavily",
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
        logger.warning(
            "Tavily search failed for %s (%s): %s",
            company_name,
            stock_code,
            type(exc).__name__,
        )
        return json.dumps(
            {
                "ok": False,
                "source": "tavily",
                "company_name": company_name,
                "stock_code": stock_code,
                "query": query,
                "error_type": type(exc).__name__,
                "message": "Tavily search failed; live news is unavailable for this run.",
                "results": [],
            },
            ensure_ascii=False,
        )
