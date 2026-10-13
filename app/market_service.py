"""Shared public cache with single-flight requests and bounded provider concurrency."""

import asyncio
import copy
import json
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from .market_data import Throttled, now


class MarketService:
    def __init__(self):
        self.lock = threading.RLock()
        self.cache = OrderedDict()
        self.flights = {}
        self.pools = {}
        self.cooldown = {}
        self.generation = 0

    async def get(self, key, provider, ttl, load):
        moment = time.monotonic()
        with self.lock:
            cached = self.cache.get(key)
            if cached and moment - cached[0] < ttl:
                self.cache.move_to_end(key)
                return copy.deepcopy(cached[1])
            until = self.cooldown.get(provider, 0)
            if moment < until:
                return self.failure(
                    cached,
                    "Provider is cooling down after rate limiting",
                    int(until - moment) + 1,
                )
            future = self.flights.get(key)
            if future is None:
                if len(self.flights) >= 64:
                    return self.failure(cached, "Market request capacity reached", 5)
                if provider not in self.pools:
                    self.pools[provider] = ThreadPoolExecutor(
                        max_workers=4, thread_name_prefix="market-" + provider
                    )
                pool = self.pools[provider]
                generation = self.generation

                def bounded_load():
                    value = load()
                    size = len(json.dumps(value, allow_nan=False).encode())
                    if size > 2 * 1024 * 1024:
                        raise ValueError("Market response exceeds limit")
                    return value, size

                future = pool.submit(bounded_load)
                self.flights[key] = future

                def complete(done):
                    with self.lock:
                        if generation != self.generation:
                            return
                        self.flights.pop(key, None)
                        try:
                            value, size = done.result()
                            self.cache[key] = (time.monotonic(), value, size)
                            while (
                                len(self.cache) > 256
                                or sum(entry[2] for entry in self.cache.values())
                                > 16 * 1024 * 1024
                            ):
                                self.cache.popitem(last=False)
                        except Throttled as error:
                            self.cooldown[provider] = time.monotonic() + error.seconds
                        except Exception:  # noqa: BLE001 -- subscriber receives sanitized failure below
                            return

                future.add_done_callback(complete)
        try:
            value, _ = await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(future)), 30
            )
            return copy.deepcopy(value)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 -- public API boundary must sanitize provider errors
            return self.failure(
                cached,
                "Market provider temporarily unavailable",
                error.seconds if isinstance(error, Throttled) else 30,
            )

    @staticmethod
    def failure(cached, message, retry):
        if cached and time.monotonic() - cached[0] < 3600:
            return {
                **copy.deepcopy(cached[1]),
                "status": "stale",
                "stale": True,
                "error": message,
                "retry_after": retry,
            }
        return {
            "status": "unavailable",
            "data": None,
            "source": None,
            "observed_at": None,
            "fetched_at": now(),
            "coverage": "Provider unavailable",
            "stale": False,
            "error": message,
            "retry_after": retry,
        }

    def close(self):
        with self.lock:
            self.generation += 1
            pools = list(self.pools.values())
            self.pools.clear()
            self.flights.clear()
            self.cache.clear()
            self.cooldown.clear()
        for pool in pools:
            pool.shutdown(wait=False, cancel_futures=True)


market_service = MarketService()
