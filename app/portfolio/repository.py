"""Legacy PostgreSQL source retained for migration compatibility tests only.

Application bootstraps use SQLitePortfolioRepository instead.
"""

import copy
import hashlib
from psycopg.types.json import Jsonb


class PortfolioRepository:
    def __init__(self, pool):
        self.pool = pool

    async def setup(self):
        async with self.pool.connection() as conn:
            await conn.execute("""CREATE TABLE IF NOT EXISTS portfolio_documents (
                org_id text NOT NULL, user_id text NOT NULL, id text NOT NULL,
                revision bigint NOT NULL, body jsonb NOT NULL,
                PRIMARY KEY (org_id,user_id,id))""")
    async def load(self, org, user, pid):
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT body FROM portfolio_documents WHERE org_id=%s AND user_id=%s AND id=%s",
                (org, user, pid),
            )
            row = await cur.fetchone()
            return row[0] if row else None

    async def list(self, org, user):
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT body FROM portfolio_documents WHERE org_id=%s AND user_id=%s ORDER BY body->>'saved_at' DESC LIMIT 100",
                (org, user),
            )
            return [row[0] for row in await cur.fetchall()]

    async def mutate(self, org, user, pid, change, expected=None):
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


