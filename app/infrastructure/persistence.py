"""SQLite Research memory and checkpoints using LangGraph's async adapters."""

import asyncio
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.sqlite.aio import AsyncSqliteStore

__all__ = ["AsyncSqliteSaver", "AsyncSqliteStore", "prepare_research_path", "configure_sqlite"]


async def prepare_research_path(path: Path) -> str:
    """Create the local directory without blocking the application event loop."""
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    return str(path)


async def configure_sqlite(resource: AsyncSqliteStore | AsyncSqliteSaver) -> None:
    """Bound lock waits and retain committed state across process restarts."""
    await resource.conn.execute("PRAGMA busy_timeout=5000")
    await resource.conn.execute("PRAGMA journal_mode=WAL")
    await resource.conn.execute("PRAGMA synchronous=FULL")
    await resource.conn.commit()
