"""Database and native connection lifecycle regressions without live services."""

import asyncio
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from app.infrastructure.native_engine import NativeEngine
from app.infrastructure.persistence import AsyncSqliteStore, AsyncSqliteSaver, configure_sqlite


class DatabaseContextLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_releases_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            for adapter in (AsyncSqliteStore, AsyncSqliteSaver):
                async with adapter.from_conn_string(
                    str(Path(temporary) / "research.sqlite3")
                ) as resource:
                    await configure_sqlite(resource)
                    await resource.setup()
                with self.assertRaises(ValueError):
                    await resource.conn.execute("SELECT 1")

    async def test_failure_closes_connection(self):
        with tempfile.TemporaryDirectory() as temporary:
            for adapter in (AsyncSqliteStore, AsyncSqliteSaver):
                with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                    async with adapter.from_conn_string(
                        str(Path(temporary) / "research.sqlite3")
                    ) as resource:
                        await resource.setup()
                        raise RuntimeError("fixture failure")
                with self.assertRaises(ValueError):
                    await resource.conn.execute("SELECT 1")

    async def test_cancelled_operation_releases_connection(self):
        with tempfile.TemporaryDirectory() as temporary:
            for adapter in (AsyncSqliteStore, AsyncSqliteSaver):
                opened = asyncio.Event()
                resources = []

                async def operation():
                    async with adapter.from_conn_string(
                        str(Path(temporary) / "research.sqlite3")
                    ) as resource:
                        await resource.setup()
                        resources.append(resource)
                        opened.set()
                        await asyncio.Event().wait()

                task = asyncio.create_task(operation())
                try:
                    await asyncio.wait_for(opened.wait(), 3)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                self.assertTrue(task.cancelled())
                with self.assertRaises(ValueError):
                    await resources[0].conn.execute("SELECT 1")


class NativeConnectionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with patch.dict("os.environ", {"ENGINE_COMMAND": '["fixture-engine"]'}):
            self.engine = NativeEngine()
        self.addAsyncCleanup(self.engine.close)

    async def pending_connection(self):
        ready = asyncio.get_running_loop().create_future()
        stop = asyncio.Event()
        task = asyncio.create_task(stop.wait())
        self.addAsyncCleanup(self.cancel_task, task)
        entry = (ready, stop, task)
        self.engine.connections["org"] = entry
        return entry

    async def cancel_task(self, task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_finished_connections_do_not_exhaust_organization_capacity(self):
        for index in range(8):
            ready = asyncio.get_running_loop().create_future()
            ready.set_result({})
            stop = asyncio.Event()
            stop.set()
            task = asyncio.create_task(stop.wait())
            await task
            self.engine.connections[f"old-{index}"] = (ready, stop, task)

        tools = {"fixture": Mock()}

        async def connect(org, ready, stop):
            ready.set_result(tools)
            await stop.wait()

        with patch.object(self.engine, "_connection", side_effect=connect):
            self.assertIs(await self.engine.tools("new-org"), tools)
        self.assertEqual(set(self.engine.connections), {"new-org"})

    async def test_shared_waiters_receive_same_tools(self):
        ready, _, _ = await self.pending_connection()
        waiters = [asyncio.create_task(self.engine.tools("org")) for _ in range(2)]
        for waiter in waiters:
            self.addAsyncCleanup(self.cancel_task, waiter)
        await asyncio.sleep(0)
        tools = {"fixture": Mock()}
        ready.set_result(tools)
        results = await asyncio.wait_for(asyncio.gather(*waiters), 1)
        self.assertTrue(all(result is tools for result in results))
        self.assertIs(self.engine.connections["org"][0], ready)

    async def test_matching_failed_connection_is_removed(self):
        ready, stop, task = await self.pending_connection()
        waiter = asyncio.create_task(self.engine.tools("org"))
        self.addAsyncCleanup(self.cancel_task, waiter)
        await asyncio.sleep(0)
        stop.set()
        await task
        error = RuntimeError("connection failed")
        ready.set_exception(error)
        with self.assertRaises(RuntimeError) as raised:
            await asyncio.wait_for(waiter, 1)
        self.assertIs(raised.exception, error)
        self.assertNotIn("org", self.engine.connections)

    async def test_old_failed_waiters_preserve_reconnected_organization(self):
        ready, stop, task = await self.pending_connection()
        waiters = [asyncio.create_task(self.engine.tools("org")) for _ in range(2)]
        for waiter in waiters:
            self.addAsyncCleanup(self.cancel_task, waiter)
        await asyncio.sleep(0)
        stop.set()
        await task
        tools = {"fixture": Mock()}

        async def connect(org, new_ready, new_stop):
            new_ready.set_result(tools)
            await new_stop.wait()

        with patch.object(self.engine, "_connection", side_effect=connect) as connection:
            self.assertIs(await self.engine.tools("org"), tools)
            replacement = self.engine.connections["org"]
            ready.set_exception(RuntimeError("old connection failed"))
            results = await asyncio.wait_for(asyncio.gather(*waiters, return_exceptions=True), 1)
            self.assertTrue(all(isinstance(result, RuntimeError) for result in results))
            self.assertIs(self.engine.connections.get("org"), replacement)
            self.assertIs(await self.engine.tools("org"), tools)
            connection.assert_called_once()

    async def test_cancelled_waiter_keeps_shared_connection(self):
        ready, _, _ = await self.pending_connection()
        waiter = asyncio.create_task(self.engine.tools("org"))
        self.addAsyncCleanup(self.cancel_task, waiter)
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertFalse(ready.cancelled())
        self.assertIs(self.engine.connections["org"][0], ready)
        ready.set_result({})
        self.assertEqual(await self.engine.tools("org"), {})
