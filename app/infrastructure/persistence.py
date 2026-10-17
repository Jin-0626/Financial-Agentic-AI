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
    opening = asyncio.create_task(asyncio.to_thread(context.__enter__))
    try:
        resource = await asyncio.shield(opening)
    except asyncio.CancelledError:
        # The thread cannot be cancelled; release its result before propagating.
        try:
            await opening
        except Exception:
            logging.getLogger(__name__).exception("PostgreSQL context failed during cancellation")
        else:
            await asyncio.to_thread(context.__exit__, None, None, None)
        raise
    try:
        yield resource
    except BaseException as error:
        await asyncio.to_thread(context.__exit__, type(error), error, error.__traceback__)
        raise
    else:
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
