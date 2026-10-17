import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.portfolio.sqlite_repository import SQLitePortfolioRepository
from app.portfolio.service import create_portfolio, Portfolio


class SQLitePortfolioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "portfolios.sqlite3"
        self.repo = SQLitePortfolioRepository(self.path)
        await self.repo.setup()

    async def asyncTearDown(self):
        await self.repo.close()
        self.directory.cleanup()

    async def test_reopen_isolation_and_atomic_updates(self):
        doc = await create_portfolio(self.repo, "org", "user", Portfolio(name="Local"))
        other = SQLitePortfolioRepository(self.path)
        await other.setup()
        try:

            async def change(old, count):
                await asyncio.sleep(0.005)
                old["updates"] = old.get("updates", 0) + 1
                old["exact"] = "9007199254740993.000000001"
                return old

            await asyncio.gather(
                *(r.mutate("org", "user", doc["id"], change) for r in [self.repo, other] * 4)
            )
            loaded = await other.load("org", "user", doc["id"])
            self.assertEqual(loaded["updates"], 8)
            self.assertEqual(loaded["revision"], 9)
            self.assertEqual(loaded["exact"], "9007199254740993.000000001")
            self.assertIsNone(await other.load("org", "different", doc["id"]))
            with self.assertRaises(ValueError):
                await other.mutate("org", "user", doc["id"], change, 1)
        finally:
            await other.close()
        await self.repo.close()
        self.repo = SQLitePortfolioRepository(self.path)
        await self.repo.setup()
        self.assertEqual((await self.repo.list("org", "user"))[0]["revision"], 9)

    async def test_callback_failure_and_cancellation_roll_back(self):
        doc = await create_portfolio(self.repo, "o", "u", Portfolio(name="Stable"))

        async def fail(old, count):
            old["portfolio"]["name"] = "Broken"
            raise ValueError("failed calculation")

        with self.assertRaises(ValueError):
            await self.repo.mutate("o", "u", doc["id"], fail)
        entered = asyncio.Event()

        async def wait(old, count):
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(self.repo.mutate("o", "u", doc["id"], wait))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(await self.repo.load("o", "u", doc["id"]), doc)

    async def test_migration_is_repeatable_and_conflicts_roll_back(self):
        doc = {"id": "legacy", "revision": 7, "saved_at": "2026-10-16", "transactions": []}
        rows = [("o", "u", "legacy", 7, doc)]
        self.assertEqual(await self.repo.import_documents(rows), 1)
        self.assertEqual(await self.repo.import_documents(rows), 0)
        extra = {**doc, "id": "extra"}
        with self.assertRaises(ValueError):
            await self.repo.import_documents(
                [
                    ("o", "u", "extra", 7, extra),
                    ("o", "u", "legacy", 7, {**doc, "different": True}),
                ]
            )
        self.assertIsNone(await self.repo.load("o", "u", "extra"))
        self.assertEqual(await self.repo.load("o", "u", "legacy"), doc)

    async def test_future_schema_is_rejected(self):
        await self.repo.close()
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("PRAGMA user_version=99")
        self.repo = SQLitePortfolioRepository(self.path)
        with self.assertRaisesRegex(RuntimeError, "Unsupported"):
            await self.repo.setup()

    async def test_failed_write_and_delete_preserve_revisions(self):
        doc = await create_portfolio(self.repo, "o", "u", Portfolio(name="Stable"))
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("""CREATE TRIGGER reject_update BEFORE UPDATE ON portfolio_documents
                BEGIN SELECT RAISE(ABORT, 'fixture disk write failure'); END""")

        async def change(old, count):
            old["portfolio"]["name"] = "Lost draft"
            return old

        with self.assertRaisesRegex(RuntimeError, "SQLite operation failed"):
            await self.repo.mutate("o", "u", doc["id"], change, doc["revision"])
        self.assertEqual(await self.repo.load("o", "u", doc["id"]), doc)

        async def delete(old, count):
            return None

        await self.repo.mutate("o", "u", doc["id"], delete, doc["revision"])
        self.assertEqual(await self.repo.list("o", "u"), [])

    async def test_limit_noop_and_list_order(self):
        for index in range(50):
            await create_portfolio(self.repo, "o", "u", Portfolio(name=f"Portfolio {index}"))
        with self.assertRaisesRegex(ValueError, "at most 50"):
            await create_portfolio(self.repo, "o", "u", Portfolio(name="Over limit"))
        rows = await self.repo.list("o", "u")
        self.assertEqual(len(rows), 50)
        self.assertEqual(rows[0]["portfolio"]["name"], "Portfolio 49")

        async def unchanged(old, count):
            return old

        doc = rows[0]
        self.assertEqual(await self.repo.mutate("o", "u", doc["id"], unchanged), doc)
        self.assertEqual((await self.repo.load("o", "u", doc["id"]))["revision"], 1)
