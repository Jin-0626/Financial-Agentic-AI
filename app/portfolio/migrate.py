"""Explicit non-destructive PostgreSQL-to-SQLite portfolio copy."""

import asyncio
import psycopg

from app.portfolio.sqlite_repository import SQLitePortfolioRepository


def read_documents(dsn):
    # Sync psycopg on the worker works on both Windows Proactor and Unix loops.
    with psycopg.connect(dsn, connect_timeout=5, options="-c statement_timeout=10000") as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            if conn.execute("SELECT to_regclass('portfolio_documents')").fetchone()[0] is None:
                return []
            return conn.execute(
                "SELECT org_id,user_id,id,revision,body FROM portfolio_documents"
            ).fetchall()


async def migrate():
    from app.infrastructure.config import DB_URL

    repository = SQLitePortfolioRepository()
    try:
        await repository.setup()
        documents = await asyncio.to_thread(read_documents, DB_URL)
        return await repository.import_documents(documents)
    finally:
        await repository.close()


if __name__ == "__main__":
    try:
        count = asyncio.run(migrate())
    except Exception:
        raise SystemExit("Portfolio migration failed; source records were not modified. Check source connectivity and target conflicts.") from None
    print(f"Copied {count} portfolio documents to SQLite; source records retained.")
