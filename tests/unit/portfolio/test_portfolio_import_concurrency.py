"""Bounded import quote retrieval preserves preview/commit integrity."""

import asyncio
import hashlib
import json
import threading
import unittest
from unittest.mock import AsyncMock, patch

import app.portfolio.service as service

import app.infrastructure.providers as providers
from tests.support.portfolio import MemoryPortfolioRepository


def request(symbols):
    return service.ImportRequest(
        data={
            "portfolio_name": "Import",
            "currency": "USD",
            "transactions": [
                {
                    "id": str(i),
                    "symbol": symbol,
                    "type": "BUY",
                    "quantity": "1",
                    "price": "10",
                    "date": "2025-01-01",
                }
                for i, symbol in enumerate(symbols)
            ],
        }
    )


class ImportConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_limit_deduplication_and_identical_preview(self):
        lock = threading.Lock()
        release = threading.Event()
        started = threading.Event()
        active = peak = 0
        seen = []

        def quote(symbol):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                seen.append(symbol)
                if active == 4:
                    started.set()
            release.wait(5)
            with lock:
                active -= 1
            return {"currency": "USD"}

        req = request([f"S{i}" for i in range(9)] + ["S0"])
        ledger = AsyncMock(return_value={"transactions": req.data["transactions"]})
        with patch.object(providers, "quote", quote), patch.object(service, "ledger", ledger):
            task = asyncio.create_task(
                service.import_preview(MemoryPortfolioRepository(), "o", "u", None, req)
            )
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 3))
                self.assertEqual(len(seen), 4)
            finally:
                release.set()
            concurrent = await task
        self.assertEqual(peak, 4)
        self.assertEqual(sorted(seen), [f"S{i}" for i in range(9)])
        with (
            patch.object(providers, "quote", return_value={"currency": "USD"}),
            patch.object(service, "ledger", ledger),
        ):
            sequential = await service.import_preview(
                MemoryPortfolioRepository(), "o", "u", None, req
            )
        self.assertEqual(concurrent, sequential)
        expected_hash = hashlib.sha256(
            json.dumps([req.mode, req.data, 0], sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        self.assertEqual(concurrent["preview_hash"], expected_hash)

    async def test_failure_order_currency_and_no_writes(self):
        for first_currency in ("EUR", "USD"):
            failed = threading.Event()
            seen = []

            def quote(symbol):
                seen.append(symbol)
                if symbol == "S0":
                    failed.wait(3)
                    return {"currency": first_currency}
                failed.set()
                raise ValueError("provider failed " + symbol)

            repo = MemoryPortfolioRepository()
            ledger = AsyncMock()
            req = request(["S0", "S1", "S2", "S3", "S4"])
            expected = (
                "Import quote currency differs" if first_currency == "EUR" else "provider failed S1"
            )
            with patch.object(providers, "quote", quote), patch.object(service, "ledger", ledger):
                with self.assertRaisesRegex(ValueError, expected):
                    await service.import_commit(repo, "o", "u", None, req)
            ledger.assert_not_awaited()
            self.assertEqual(sorted(seen), ["S0", "S1", "S2", "S3"])
            self.assertEqual(await repo.list("o", "u"), [])

    async def test_cancel_drains_started_threads_and_stops_next_batch(self):
        release = threading.Event()
        started = threading.Event()
        lock = threading.Lock()
        seen = []

        def quote(symbol):
            with lock:
                seen.append(symbol)
                if len(seen) == 4:
                    started.set()
            release.wait(5)
            return {"currency": "USD"}

        ledger = AsyncMock()
        repo = MemoryPortfolioRepository()
        with patch.object(providers, "quote", quote), patch.object(service, "ledger", ledger):
            task = asyncio.create_task(
                service.import_commit(repo, "o", "u", None, request([f"S{i}" for i in range(9)]))
            )
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 3))
                task.cancel()
                await asyncio.sleep(0.02)
                self.assertFalse(task.done())
                task.cancel()
                await asyncio.sleep(0.02)
                self.assertFalse(task.done())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(len(seen), 4)
        ledger.assert_not_awaited()
        self.assertEqual(await repo.list("o", "u"), [])
