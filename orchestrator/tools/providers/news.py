from __future__ import annotations
import os
import requests
import logging
from urllib.parse import urlsplit, urlencode
from typing import Any
from datetime import datetime, timedelta, UTC
from orchestrator.freshness import now_utc
from langchain_core.tools import BaseTool, tool
from app.infrastructure.diagnostics import sanitize_error
from . import common

logger = logging.getLogger(__name__)


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
                published = datetime.fromtimestamp(raw, UTC)
            elif isinstance(raw, str):
                try:
                    published = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except ValueError:
                    from email.utils import parsedate_to_datetime

                    published = parsedate_to_datetime(raw)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=UTC)
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
        key=lambda pair: pair[0] or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    return [item for _, item in result]


def _make_news_tool() -> BaseTool:
    @tool
    def financial_news(
        symbol: str | None = None,
        count: int = 10,
        query: str | None = None,
        days: int | None = 7,
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
            return common._to_json(
                {
                    "status": "error",
                    "category": "validation",
                    "error": "days must be 1 to 3650, or null for historical research",
                    "articles": [],
                }
            )
        if not 1 <= count <= 20:
            return common._to_json(
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
                part for part in [symbol, query or "latest financial business news"] if part
            )
            try:
                articles = _recent_news(
                    _search_tavily_news(search_query, count, tavily_key, days), days
                )
                if articles:
                    return common._to_json(articles)
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
                return common._to_json(
                    {
                        "articles": articles,
                        "status": "partial_success",
                        "attempts": attempts,
                    }
                )
            return common._to_json(articles)

        # Ticker feeds do not implement free-text search. Never substitute unrelated
        # ticker/latest headlines for a user's explicit research query.
        if not query and common._fmp_client and getattr(common._fmp_client, "api_key", None):
            try:
                params = {"limit": count}
                if symbol:
                    params["symbols"] = symbol.upper()
                endpoint = "news/stock" if symbol else "news/stock-latest"
                news = common._fmp_client._request(endpoint, params, version="stable")
                if not isinstance(news, list):
                    raise ValueError("FMP returned a non-list news response")
                news = _recent_news(news, days)
                if news:
                    return success(news)
                attempts.append({"provider": "FMP", "status": "empty"})
            except Exception as e:
                attempts.append({"provider": "FMP", "status": "error", "error": sanitize_error(e)})
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
        return common._to_json(result)

    return financial_news
