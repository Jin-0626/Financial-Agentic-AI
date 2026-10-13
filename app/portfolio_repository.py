"""Transactional portfolio documents, isolated from LangGraph's memory store."""

import asyncio
import copy
import hashlib
from psycopg.types.json import Jsonb


class PortfolioRepository:
    def __init__(self, pool, legacy_store):
        self.pool = pool
        self.legacy_store = legacy_store

    async def setup(self):
        async with self.pool.connection() as conn:
            await conn.execute("""CREATE TABLE IF NOT EXISTS portfolio_documents (
                org_id text NOT NULL, user_id text NOT NULL, id text NOT NULL,
                revision bigint NOT NULL, body jsonb NOT NULL, legacy_backup jsonb,
                PRIMARY KEY (org_id,user_id,id))""")
            await conn.execute("""CREATE TABLE IF NOT EXISTS portfolio_legacy_migrations (
                org_id text NOT NULL, user_id text NOT NULL, id text NOT NULL,
                PRIMARY KEY(org_id,user_id,id))""")

    async def migrate(self, org, user):
        # The original LangGraph records remain untouched and also have a SQL backup.
        items = await self.legacy_store.asearch(("portfolios", org, user), limit=100)
        async with self.pool.connection() as conn:
            async with conn.transaction():
                for item in items:
                    if (
                        item.namespace != ("portfolios", org, user)
                        or "portfolio" not in item.value
                    ):
                        continue
                    marker = await conn.execute(
                        "INSERT INTO portfolio_legacy_migrations(org_id,user_id,id) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id",
                        (org, user, item.key),
                    )
                    if await marker.fetchone() is None:
                        continue
                    value = copy.deepcopy(item.value)
                    value.update(id=item.key, legacy_opening_unknown=True)
                    await conn.execute(
                        "INSERT INTO portfolio_documents (org_id,user_id,id,revision,body,legacy_backup) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        (
                            org,
                            user,
                            item.key,
                            value.get("revision", 0),
                            Jsonb(value),
                            Jsonb(item.value),
                        ),
                    )

    async def load(self, org, user, pid):
        await self.migrate(org, user)
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT body FROM portfolio_documents WHERE org_id=%s AND user_id=%s AND id=%s",
                (org, user, pid),
            )
            row = await cur.fetchone()
            return row[0] if row else None

    async def list(self, org, user):
        await self.migrate(org, user)
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT body FROM portfolio_documents WHERE org_id=%s AND user_id=%s ORDER BY body->>'saved_at' DESC LIMIT 100",
                (org, user),
            )
            return [row[0] for row in await cur.fetchall()]

    async def mutate(self, org, user, pid, change, expected=None):
        await self.migrate(org, user)
        async with self.pool.connection() as conn:
            async with conn.transaction():
                # Also serializes creation and portfolio-count checks, across workers.
                lock = int.from_bytes(
                    hashlib.sha256(f"{org}\0{user}".encode()).digest()[:8],
                    "big",
                    signed=True,
                )
                await conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock,))
                cur = await conn.execute(
                    "SELECT body FROM portfolio_documents WHERE org_id=%s AND user_id=%s AND id=%s FOR UPDATE",
                    (org, user, pid),
                )
                row = await cur.fetchone()
                old = row[0] if row else None
                if expected is not None and (
                    old is None or old.get("revision", 0) != expected
                ):
                    raise ValueError("Portfolio changed; reload before saving")
                cur = await conn.execute(
                    "SELECT count(*) FROM portfolio_documents WHERE org_id=%s AND user_id=%s",
                    (org, user),
                )
                count = (await cur.fetchone())[0]
                new = await change(copy.deepcopy(old), count)
                if new == old:
                    return old
                if new is None:
                    await conn.execute(
                        "DELETE FROM portfolio_documents WHERE org_id=%s AND user_id=%s AND id=%s",
                        (org, user, pid),
                    )
                else:
                    new["revision"] = (old or {}).get("revision", 0) + 1
                    await conn.execute(
                        "INSERT INTO portfolio_documents(org_id,user_id,id,revision,body) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(org_id,user_id,id) DO UPDATE SET revision=EXCLUDED.revision,body=EXCLUDED.body",
                        (org, user, pid, new["revision"], Jsonb(new)),
                    )
                return new


class MemoryPortfolioRepository:
    """Deterministic test repository; production always uses PostgreSQL."""

    def __init__(self):
        self.data = {}
        self.lock = asyncio.Lock()

    async def load(self, org, user, pid):
        return copy.deepcopy(self.data.get((org, user, pid)))

    async def list(self, org, user):
        return [
            copy.deepcopy(v)
            for (o, u, _), v in self.data.items()
            if (o, u) == (org, user)
        ]

    async def mutate(self, org, user, pid, change, expected=None):
        async with self.lock:
            old = await self.load(org, user, pid)
            if expected is not None and (
                old is None or old.get("revision", 0) != expected
            ):
                raise ValueError("Portfolio changed; reload before saving")
            new = await change(old, len(await self.list(org, user)))
            if new == old:
                return old
            if new is None:
                self.data.pop((org, user, pid), None)
            else:
                new["revision"] = (old or {}).get("revision", 0) + 1
                self.data[(org, user, pid)] = copy.deepcopy(new)
            return new
