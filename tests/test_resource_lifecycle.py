"""Database and native connection lifecycle regressions without live services."""

import asyncio
import unittest
from unittest.mock import Mock, patch

from app.native_engine import NativeEngine
from app.persistence import ThreadedPool


class ThreadedPoolLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with patch("app.persistence.ConnectionPool") as constructor:
            self.pool = ThreadedPool("fixture", open=False)
        self.sync_pool = constructor.return_value

    async def test_success_closes_on_context_exit(self):
        async with self.pool as opened:
            self.assertIs(opened, self.pool)
            self.sync_pool.close.assert_not_called()
        self.sync_pool.open.assert_called_once_with()
        self.sync_pool.wait.assert_called_once_with()
        self.sync_pool.close.assert_called_once_with()

    async def test_startup_failure_closes_and_preserves_original_error(self):
        for stage in ("open", "wait"):
            with self.subTest(stage=stage):
                self.sync_pool.reset_mock(side_effect=True)
                error = RuntimeError(f"{stage} failed")
                getattr(self.sync_pool, stage).side_effect = error
                with self.assertRaises(RuntimeError) as raised:
                    async with self.pool:
                        self.fail("Failed startup must not enter the context")
                self.assertIs(raised.exception, error)
                self.sync_pool.close.assert_called_once_with()

    async def test_cleanup_failure_does_not_replace_startup_error(self):
        error = RuntimeError("startup failed")
        self.sync_pool.wait.side_effect = error
        self.sync_pool.close.side_effect = RuntimeError("cleanup failed")
        with self.assertLogs("app.persistence", level="ERROR"):
            with self.assertRaises(RuntimeError) as raised:
                await self.pool.__aenter__()
        self.assertIs(raised.exception, error)
        self.sync_pool.close.assert_called_once_with()

    async def test_cancelled_readiness_closes_before_propagating(self):
        waiting = asyncio.Event()

        async def wait():
            waiting.set()
            await asyncio.Event().wait()

        with patch.object(self.pool, "wait", wait):
            task = asyncio.create_task(self.pool.__aenter__())
            self.addAsyncCleanup(self.cancel_task, task)
            await asyncio.wait_for(waiting.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.sync_pool.close.assert_called_once_with()

    async def test_cancelled_open_finishes_before_cleanup(self):
        started = asyncio.Event()
        finish = asyncio.Event()
        events = []

        async def open_pool():
            started.set()
            await finish.wait()
            events.append("opened")

        async def close_pool():
            events.append("closed")

        with (
            patch.object(self.pool, "open", open_pool),
            patch.object(self.pool, "close", close_pool),
        ):
            task = asyncio.create_task(self.pool.__aenter__())
            self.addAsyncCleanup(self.cancel_task, task)
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            await asyncio.sleep(0)
            self.assertEqual(events, [])
            finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
        self.assertEqual(events, ["opened", "closed"])
        self.sync_pool.wait.assert_not_called()

    async def cancel_task(self, task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


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
            results = await asyncio.wait_for(
                asyncio.gather(*waiters, return_exceptions=True), 1
            )
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
