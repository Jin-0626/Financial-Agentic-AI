import asyncio, os, unittest, uuid
from unittest.mock import AsyncMock
from app.infrastructure.persistence import AsyncConnectionPool
from app.portfolio.repository import PortfolioRepository
from app.portfolio.service import create_portfolio, Portfolio, delete_portfolio
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
        self.repo = PortfolioRepository(self.pool)
        await self.repo.setup()

    async def asyncTearDown(self):
        async with self.pool.connection() as conn:
            await conn.execute(
                "DELETE FROM portfolio_documents WHERE org_id=%s", (self.org,)
            )
        await self.pool.close()

    async def test_independent_connections_cannot_lose_updates_and_reload(self):
        doc = await create_portfolio(
            self.repo, self.org, self.user, Portfolio(name="Persistent")
        )
        other = PortfolioRepository(self.pool)

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


