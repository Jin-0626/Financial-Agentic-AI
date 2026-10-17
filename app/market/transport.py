import logging
import math
import os
import threading
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
import requests
import yfinance as yf

__all__ = ["yf", "fetch", "Throttled", "yahoo_bound", "cg"]

logger = logging.getLogger(__name__)

HOSTS = {
    "api.coingecko.com",
    "api.nasdaq.com",
    "api.stlouisfed.org",
    "api.bnm.gov.my",
    "nfs.faireconomy.media",
    "www.ecb.europa.eu",
    "financialmodelingprep.com",
}


_HTTP_COOLDOWN = {}


_HTTP_LOCK = threading.Lock()


_HTTP_SLOTS = {host: threading.BoundedSemaphore(4) for host in HOSTS}


_YAHOO_SLOTS = threading.BoundedSemaphore(4)


class Throttled(RuntimeError):
    def __init__(self, seconds=60):
        self.seconds = max(1, min(seconds, 300))
        super().__init__("Provider rate limit reached")


def fetch(url, params=None, headers=None):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in HOSTS
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Provider destination is not allowed")
    with _HTTP_SLOTS[parsed.hostname], requests.Session() as session:
        with _HTTP_LOCK:
            remaining = _HTTP_COOLDOWN.get(parsed.hostname, 0) - time.monotonic()
        if remaining > 0:
            raise Throttled(math.ceil(remaining))
        with session.get(
            url,
            params=params,
            headers=headers,
            timeout=(5, 15),
            stream=True,
            allow_redirects=False,
        ) as response:
            if response.status_code == 429:
                retry = response.headers.get("Retry-After", "60")
                try:
                    seconds = int(retry)
                except ValueError:
                    try:
                        seconds = int(
                            (parsedate_to_datetime(retry) - datetime.now(UTC)).total_seconds()
                        )
                    except (ValueError, TypeError):
                        seconds = 60
                error = Throttled(seconds)
                with _HTTP_LOCK:
                    _HTTP_COOLDOWN[parsed.hostname] = time.monotonic() + error.seconds
                raise error
            response.raise_for_status()
            if 300 <= response.status_code < 400:
                raise ValueError("Provider redirect is not allowed")
            body = bytearray()
            for chunk in response.iter_content(65536):
                body.extend(chunk)
                if len(body) > 8 * 1024 * 1024:
                    raise ValueError("Provider response exceeds limit")
            import json

            return json.loads(body)


def yahoo_bound(fn):
    from functools import wraps

    @wraps(fn)
    def guarded(*args, **kwargs):
        with _YAHOO_SLOTS:
            return fn(*args, **kwargs)

    return guarded


def cg(path, params=None):
    key = os.getenv("COINGECKO_DEMO_API_KEY", "")
    return fetch(
        "https://api.coingecko.com/api/v3/" + path,
        params,
        {"x-cg-demo-api-key": key} if key else {},
    )
