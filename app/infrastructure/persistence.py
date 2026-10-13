"""Native async PostgreSQL; a small thread bridge handles Windows Proactor loops.

Psycopg requires selector sockets on Windows, while stdio MCP requires Proactor
subprocesses. Only database I/O crosses threads; graph/MCP cancellation stays async.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from langgraph.store.postgres import AsyncPostgresStore as NativeStore, PostgresStore
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver as NativeSaver
from psycopg_pool import AsyncConnectionPool as NativePool, ConnectionPool


def needs_bridge():
    proactor = getattr(asyncio, "ProactorEventLoop", None)
    return proactor is not None and isinstance(asyncio.get_running_loop(), proactor)


class ThreadedStore(PostgresStore):
    async def setup(self):
        await asyncio.to_thread(super().setup)


class ThreadedSaver(PostgresSaver):
    async def setup(self):
        await asyncio.to_thread(super().setup)

    async def aget_tuple(self, *args, **kwargs):
        return await asyncio.to_thread(self.get_tuple, *args, **kwargs)

    async def aput(self, *args, **kwargs):
        return await asyncio.to_thread(self.put, *args, **kwargs)

    async def aput_writes(self, *args, **kwargs):
        return await asyncio.to_thread(self.put_writes, *args, **kwargs)

    async def adelete_thread(self, *args, **kwargs):
        return await asyncio.to_thread(self.delete_thread, *args, **kwargs)

    async def aget_delta_channel_history(self, *args, **kwargs):
        return await asyncio.to_thread(self.get_delta_channel_history, *args, **kwargs)

    async def alist(self, *args, **kwargs):
        rows = await asyncio.to_thread(lambda: list(self.list(*args, **kwargs)))
        for row in rows:
            yield row


@asynccontextmanager
async def bridge_context(context):
    resource = await asyncio.to_thread(context.__enter__)
    try:
        yield resource
    finally:
        await asyncio.to_thread(context.__exit__, None, None, None)


class AsyncPostgresStore:
    @staticmethod
    def from_conn_string(*args, **kwargs):
        return (
            bridge_context(ThreadedStore.from_conn_string(*args, **kwargs))
            if needs_bridge()
            else NativeStore.from_conn_string(*args, **kwargs)
        )


class AsyncPostgresSaver:
    @staticmethod
    def from_conn_string(*args, **kwargs):
        return (
            bridge_context(ThreadedSaver.from_conn_string(*args, **kwargs))
            if needs_bridge()
            else NativeSaver.from_conn_string(*args, **kwargs)
        )


class AsyncCursor:
    def __init__(self, cursor):
        self.cursor = cursor

    async def fetchone(self):
        return await asyncio.to_thread(self.cursor.fetchone)

    async def fetchall(self):
        return await asyncio.to_thread(self.cursor.fetchall)


class AsyncConnection:
    def __init__(self, conn):
        self.conn = conn

    async def execute(self, *args, **kwargs):
        return AsyncCursor(await asyncio.to_thread(self.conn.execute, *args, **kwargs))

    @asynccontextmanager
    async def transaction(self):
        transaction = self.conn.transaction()
        await asyncio.to_thread(transaction.__enter__)
        try:
            yield
        except BaseException as error:
            await asyncio.to_thread(
                transaction.__exit__, type(error), error, error.__traceback__
            )
            raise
        else:
            await asyncio.to_thread(transaction.__exit__, None, None, None)


class ThreadedPool:
    def __init__(self, *args, **kwargs):
        self.pool = ConnectionPool(*args, **kwargs)

    async def open(self):
        await asyncio.to_thread(self.pool.open)

    async def wait(self):
        await asyncio.to_thread(self.pool.wait)

    async def close(self):
        await asyncio.to_thread(self.pool.close)

    async def __aenter__(self):
        # to_thread keeps running after cancellation; finish opening before closing.
        opening = asyncio.create_task(self.open())
        try:
            await asyncio.shield(opening)
            await self.wait()
        except BaseException:
            try:
                await asyncio.shield(opening)
            except BaseException:
                pass
            try:
                await self.close()
            except Exception:
                logging.getLogger(__name__).error(
                    "Failed to close PostgreSQL pool after startup failure"
                )
            raise
        return self

    async def __aexit__(self, *args):
        await self.close()

    @asynccontextmanager
    async def connection(self):
        cm = self.pool.connection()
        conn = await asyncio.to_thread(cm.__enter__)
        try:
            yield AsyncConnection(conn)
        except BaseException as error:
            await asyncio.to_thread(
                cm.__exit__, type(error), error, error.__traceback__
            )
            raise
        else:
            await asyncio.to_thread(cm.__exit__, None, None, None)


def AsyncConnectionPool(*args, **kwargs):
    return (
        ThreadedPool(*args, **kwargs) if needs_bridge() else NativePool(*args, **kwargs)
    )
