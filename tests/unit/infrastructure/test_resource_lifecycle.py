"""Database and native connection lifecycle regressions without live services."""

import asyncio
import unittest
from unittest.mock import MagicMock, Mock, patch

from app.infrastructure.native_engine import NativeEngine
from app.infrastructure.persistence import bridge_context


class DatabaseContextLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_releases_context(self):
        context = MagicMock()
        async with bridge_context(context) as resource:
            self.assertIs(resource, context.__enter__.return_value)
        context.__exit__.assert_called_once_with(None, None, None)

    async def test_failure_reaches_transaction_context(self):
        context = MagicMock()
        error = RuntimeError("transaction failed")
        with self.assertRaises(RuntimeError) as raised:
            async with bridge_context(context):
                raise error
        self.assertIs(raised.exception, error)
        self.assertIs(context.__exit__.call_args.args[1], error)

    async def test_cancelled_open_releases_acquired_resource(self):
        import threading

        started = threading.Event()
        finish = threading.Event()
        events = []
        context = MagicMock()

        def enter():
            started.set()
            finish.wait(3)
            events.append("opened")

        context.__enter__.side_effect = enter
        context.__exit__.side_effect = lambda *args: events.append("closed")

        async def acquire():
            async with bridge_context(context):
                self.fail("cancelled open must not yield")

        task = asyncio.create_task(acquire())
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            task.cancel()
            await asyncio.sleep(0)
        finally:
            finish.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(events, ["opened", "closed"])


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
