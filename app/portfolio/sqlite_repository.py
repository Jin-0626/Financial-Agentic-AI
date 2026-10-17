"""Identity-scoped SQLite documents; SQL runs on a dedicated worker thread."""

import asyncio
import copy
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def portfolio_path():
    override = os.getenv("PORTFOLIO_SQLITE_PATH")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "financial-research-terminal" / "portfolios.sqlite3"


class SQLitePortfolioRepository:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else portfolio_path()
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="portfolio-sqlite")
        self._lock = asyncio.Lock()
        self._connection = None
        self._closed = False

    async def _call(self, operation):
        if self._closed:
            raise RuntimeError("Portfolio storage is closed")
        pending = asyncio.get_running_loop().run_in_executor(self._worker, operation)
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            try:
                await pending
            except Exception:
                pass
            raise
        except sqlite3.Error as error:
            raise RuntimeError("Portfolio SQLite operation failed") from error

    async def setup(self):
        def initialize():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            try:
                if conn.execute("PRAGMA user_version").fetchone()[0] > 1:
                    raise RuntimeError("Unsupported portfolio SQLite schema version")
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=FULL")
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("""CREATE TABLE IF NOT EXISTS portfolio_documents (
                    org_id TEXT NOT NULL, user_id TEXT NOT NULL, id TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision >= 1),
                    saved_at TEXT NOT NULL, body TEXT NOT NULL CHECK(json_valid(body)),
                    PRIMARY KEY(org_id, user_id, id))""")
                conn.execute("""CREATE INDEX IF NOT EXISTS portfolio_saved
                    ON portfolio_documents(org_id, user_id, saved_at DESC, id)""")
                conn.execute("PRAGMA user_version=1")
                conn.commit()
            except BaseException:
                conn.close()
                raise
            self._connection = conn

        async with self._lock:
            if self._connection is None:
                await self._call(initialize)

    def _conn(self):
        if self._connection is None:
            raise RuntimeError("Portfolio storage is not initialized")
        return self._connection

    def _load(self, org, user, pid):
        row = (
            self._conn()
            .execute(
                "SELECT body FROM portfolio_documents WHERE org_id=? AND user_id=? AND id=?",
                (org, user, pid),
            )
            .fetchone()
        )
        return json.loads(row[0]) if row else None

    async def load(self, org, user, pid):
        async with self._lock:
            return await self._call(lambda: self._load(org, user, pid))

    async def list(self, org, user):
        def read():
            rows = (
                self._conn()
                .execute(
                    """SELECT body FROM portfolio_documents WHERE org_id=? AND user_id=?
                   ORDER BY saved_at DESC, id LIMIT 100""",
                    (org, user),
                )
                .fetchall()
            )
            return [json.loads(row[0]) for row in rows]

        async with self._lock:
            return await self._call(read)

    def _write(self, org, user, pid, document):
        self._conn().execute(
            """INSERT INTO portfolio_documents(org_id,user_id,id,revision,saved_at,body)
               VALUES(?,?,?,?,?,?) ON CONFLICT(org_id,user_id,id) DO UPDATE SET
               revision=excluded.revision,saved_at=excluded.saved_at,body=excluded.body""",
            (
                org,
                user,
                pid,
                document["revision"],
                document.get("saved_at", ""),
                json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
            ),
        )

    async def mutate(self, org, user, pid, change, expected=None):
        async with self._lock:
            try:
                await self._call(lambda: self._conn().execute("BEGIN IMMEDIATE"))
                old = await self._call(lambda: self._load(org, user, pid))
                if expected is not None and (old is None or old.get("revision", 0) != expected):
                    raise ValueError("Portfolio changed; reload before saving")
                count = await self._call(
                    lambda: (
                        self._conn()
                        .execute(
                            "SELECT count(*) FROM portfolio_documents WHERE org_id=? AND user_id=?",
                            (org, user),
                        )
                        .fetchone()[0]
                    )
                )
                new = await change(copy.deepcopy(old), count)
                if new != old:
                    if new is None:
                        await self._call(
                            lambda: self._conn().execute(
                                "DELETE FROM portfolio_documents WHERE org_id=? AND user_id=? AND id=?",
                                (org, user, pid),
                            )
                        )
                    else:
                        new["revision"] = (old or {}).get("revision", 0) + 1
                        await self._call(lambda: self._write(org, user, pid, new))
                await self._call(lambda: self._conn().commit())
                return new
            finally:
                await self._call(lambda: self._conn().rollback())

    async def import_documents(self, documents):
        """Atomic repeatable copy; never replace differing local documents."""

        def copy_documents():
            conn = self._conn()
            imported = 0
            conn.execute("BEGIN IMMEDIATE")
            try:
                for org, user, pid, revision, document in documents:
                    if (
                        not isinstance(document, dict)
                        or document.get("id") != pid
                        or document.get("revision") != revision
                        or revision < 1
                    ):
                        raise ValueError("Invalid portfolio migration document")
                    previous = self._load(org, user, pid)
                    if previous is not None:
                        if previous != document:
                            raise ValueError("Portfolio migration conflicts with local data")
                        continue
                    self._write(org, user, pid, document)
                    imported += 1
                conn.commit()
                return imported
            finally:
                conn.rollback()

        async with self._lock:
            return await self._call(copy_documents)

    async def close(self):
        async with self._lock:
            if self._closed:
                return
            try:
                if self._connection is not None:
                    await self._call(lambda: self._connection.close())
            finally:
                self._closed = True
                self._worker.shutdown(wait=True)
