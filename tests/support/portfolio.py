"""Deterministic repository fixture; never imported by production."""
import asyncio, copy

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
