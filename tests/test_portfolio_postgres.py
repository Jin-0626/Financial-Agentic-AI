import asyncio, os, unittest, uuid
from unittest.mock import AsyncMock
from app.persistence import AsyncConnectionPool
from app.portfolio_repository import PortfolioRepository
from app.portfolio_service import create_portfolio, Portfolio, delete_portfolio
from langgraph.store.memory import InMemoryStore


@unittest.skipUnless(
    os.getenv("TEST_DB_URL"), "Set TEST_DB_URL for PostgreSQL integration"
)
class PortfolioPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.org = "test-" + uuid.uuid4().hex
        self.user = "user"
        self.store = InMemoryStore()
        self.pool = AsyncConnectionPool(
            os.environ["TEST_DB_URL"], open=False, min_size=1, max_size=4
        )
        await self.pool.open()
        await self.pool.wait()
        self.repo = PortfolioRepository(self.pool, self.store)
        await self.repo.setup()

    async def asyncTearDown(self):
        async with self.pool.connection() as conn:
            await conn.execute(
                "DELETE FROM portfolio_documents WHERE org_id=%s", (self.org,)
            )
            await conn.execute(
                "DELETE FROM portfolio_legacy_migrations WHERE org_id=%s", (self.org,)
            )
        await self.pool.close()

    async def test_independent_connections_cannot_lose_updates_and_reload(self):
        doc = await create_portfolio(
            self.repo, self.org, self.user, Portfolio(name="Persistent")
        )
        other = PortfolioRepository(self.pool, self.store)

        async def change(old, count):
            await asyncio.sleep(0.01)
            old["updates"] = old.get("updates", 0) + 1
            return old

        await asyncio.gather(
            *(
                r.mutate(self.org, self.user, doc["id"], change)
                for r in [self.repo, other] * 4
            )
        )
        loaded = await other.load(self.org, self.user, doc["id"])
        self.assertEqual(loaded["updates"], 8)
        self.assertEqual(loaded["portfolio"]["name"], "Persistent")
        self.assertIsNone(await other.load(self.org, "other", doc["id"]))
        with self.assertRaises(ValueError):
            await other.mutate(self.org, self.user, doc["id"], change, 1)

    async def test_migration_backup_and_delete_does_not_resurrect(self):
        legacy = {
            "portfolio": {
                "name": "Legacy",
                "currency": "USD",
                "benchmark": "SPY",
                "period": "1y",
                "positions": [{"symbol": "AAPL", "quantity": 2, "average_cost": 100}],
            },
            "transactions": [],
            "snapshots": [],
            "revision": 1,
        }
        self.store.put(("portfolios", self.org, self.user), "default", legacy)
        migrated = await self.repo.load(self.org, self.user, "default")
        self.assertTrue(migrated["legacy_opening_unknown"])
        self.assertEqual(
            self.store.get(("portfolios", self.org, self.user), "default").value, legacy
        )
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT legacy_backup FROM portfolio_documents WHERE org_id=%s AND user_id=%s AND id='default'",
                (self.org, self.user),
            )
            self.assertEqual((await cur.fetchone())[0], legacy)
        await delete_portfolio(self.repo, self.org, self.user, "default")
        self.assertIsNone(await self.repo.load(self.org, self.user, "default"))
        self.assertEqual(await self.repo.list(self.org, self.user), [])

    async def test_retired_report_channel_is_archived_and_migration_is_idempotent(self):
        from app.persistence import AsyncPostgresSaver
        from app.checkpoint_migration import migrate_checkpoint_contract
        from psycopg.types.json import Jsonb

        async with AsyncPostgresSaver.from_conn_string(
            os.environ["TEST_DB_URL"]
        ) as saver:
            await saver.setup()
        thread = self.org + "__" + self.user + "__legacy"
        original = {
            "v": 4,
            "id": "legacy",
            "channel_versions": {"messages": "1", "structured_response": "2"},
            "channel_values": {
                "structured_response": {"executive_summary": "Preserved"}
            },
        }
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO checkpoints(thread_id,checkpoint_ns,checkpoint_id,checkpoint,metadata) VALUES(%s,'','legacy',%s,%s)",
                (thread, Jsonb(original), Jsonb({"source": "update"})),
            )
        try:
            await migrate_checkpoint_contract(self.pool)
            await migrate_checkpoint_contract(self.pool)
            async with self.pool.connection() as conn:
                cursor = await conn.execute(
                    "SELECT checkpoint,metadata FROM checkpoints WHERE thread_id=%s",
                    (thread,),
                )
                checkpoint, metadata = await cursor.fetchone()
                self.assertNotIn("structured_response", checkpoint["channel_versions"])
                self.assertEqual(checkpoint["channel_versions"]["messages"], "1")
                self.assertEqual(metadata["org_id"], self.org)
                cursor = await conn.execute(
                    "SELECT checkpoint FROM checkpoint_legacy_contracts WHERE thread_id=%s",
                    (thread,),
                )
                self.assertEqual((await cursor.fetchone())[0], original)
        finally:
            async with self.pool.connection() as conn:
                await conn.execute(
                    "DELETE FROM checkpoint_legacy_contracts WHERE thread_id=%s",
                    (thread,),
                )
                await conn.execute(
                    "DELETE FROM checkpoints WHERE thread_id=%s", (thread,)
                )
