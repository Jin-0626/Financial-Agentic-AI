import logging
import os
import re
from urllib.parse import urlsplit
from app.market import common
from app.market import transport

logger = logging.getLogger(__name__)


@transport.yahoo_bound
def news(key=None):
    from urllib.parse import parse_qsl, urlencode, urlunsplit

    from orchestrator.tools.providers.news import _public_market_news

    asset = common.instrument(key) if key else None
    articles = []
    failures = []
    try:
        articles.extend(_public_market_news(20, asset["symbol"] if asset else None, 7))
    except Exception:  # noqa: BLE001 -- isolate third-party provider failures; expose partial coverage
        failures.append("RSS feed unavailable")
    fmp = os.getenv("FMP_API_KEY", "")
    if fmp:
        try:
            raw = transport.fetch(
                "https://financialmodelingprep.com/stable/news/stock"
                if asset
                else "https://financialmodelingprep.com/stable/news/stock-latest",
                {
                    "apikey": fmp,
                    "limit": 20,
                    **({"symbols": asset["symbol"]} if asset else {}),
                },
            )
            if not isinstance(raw, list):
                raise TypeError("Invalid FMP news response")
            articles.extend(
                {
                    "title": r.get("title"),
                    "url": r.get("url"),
                    "provider": r.get("publisher") or r.get("site"),
                    "pub_date": r.get("publishedDate"),
                }
                for r in raw[:20]
            )
        except Exception:  # noqa: BLE001 -- isolate third-party provider failures; expose partial coverage
            failures.append("FMP news unavailable")
    if asset and asset["asset_class"] != "crypto":
        try:
            for item in transport.yf.Ticker(asset["symbol"]).get_news(count=20):
                item = item.get("content", item)
                articles.append(
                    {
                        "title": item.get("title"),
                        "url": (item.get("canonicalUrl") or {}).get("url"),
                        "provider": (item.get("provider") or {}).get("displayName"),
                        "pub_date": item.get("pubDate"),
                    }
                )
        except Exception:  # noqa: BLE001 -- isolate third-party provider failures; expose partial coverage
            failures.append("Yahoo news unavailable")
    seen_urls, seen_titles = set(), set()
    unique = []
    for article in articles:
        url = article.get("url")
        title = article.get("title")
        if not isinstance(url, str) or not isinstance(title, str):
            continue
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        clean = urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode([(k, v) for k, v in parse_qsl(parsed.query) if not k.startswith("utm_")]),
                "",
            )
        )
        headline = re.sub(r"\W+", " ", title).strip().casefold()
        if clean in seen_urls or headline in seen_titles:
            continue
        seen_urls.add(clean)
        seen_titles.add(headline)
        unique.append(
            {
                "title": title,
                "url": clean,
                "publisher": article.get("provider"),
                "published_at": article.get("pub_date"),
            }
        )
    if not unique:
        return {
            **common.result(
                [],
                "Public RSS / Yahoo Finance / FMP",
                "No verified current articles returned",
                limitations=failures,
            ),
            "status": "unavailable" if failures else "ok",
        }
    return common.result(
        unique[:30],
        "Public RSS / Yahoo Finance / FMP",
        "Provider headlines; publication dates retained",
        limitations=failures,
    )
