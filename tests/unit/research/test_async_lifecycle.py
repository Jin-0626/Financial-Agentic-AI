import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock, patch
from langgraph.store.memory import InMemoryStore
from contracts.namespaces import user_namespace
from orchestrator.agent import _ensure_user_habits
from types import SimpleNamespace


class AsyncLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_memory_is_org_user_scoped_and_legacy_is_preserved(self):
        store = InMemoryStore()
        store.put(("u",), "/habits.md", {"content": "Legacy"})
        await _ensure_user_habits(store, "u", "org-a")
        await _ensure_user_habits(store, "u", "org-b")
        self.assertEqual(store.get(("u",), "/habits.md").value["content"], "Legacy")
        self.assertIsNotNone(store.get(("memories", "org-a", "u"), "/habits.md"))
        rt = SimpleNamespace(context=SimpleNamespace(org_id="org-b", user_id="u"), server_info=None)
        self.assertEqual(user_namespace(rt), ("memories", "org-b", "u"))

    async def test_startup_and_failure_close_all_resources(self):
        import app.bootstrap.application as app

        for fail in [False, True]:
            events = []
            store = Mock(setup=AsyncMock())
            saver = Mock(
                setup=AsyncMock(side_effect=RuntimeError("fixture failure") if fail else None)
            )
            engine = Mock(close=AsyncMock())
            repository = Mock(setup=AsyncMock(), close=AsyncMock())

            def resource(name, value):
                @asynccontextmanager
                async def context(*args, **kwargs):
                    events.append(name + "-open")
                    try:
                        yield value
                    finally:
                        events.append(name + "-close")

                return context

            with (
                patch.dict("os.environ", {"RESEARCH_ENABLED": "true"}),
                patch.object(app.AsyncSqliteStore, "from_conn_string", resource("store", store)),
                patch.object(app.AsyncSqliteSaver, "from_conn_string", resource("saver", saver)),
                patch.object(
                    app, "prepare_research_path", AsyncMock(return_value="fixture.sqlite3")
                ),
                patch.object(app, "configure_sqlite", AsyncMock()),
                patch.object(app, "NativeEngine", return_value=engine),
                patch.object(app, "SQLitePortfolioRepository", return_value=repository),
                patch.object(app, "_ensure_agents_memory", AsyncMock()),
                patch.object(app, "AgentRuntime", return_value=Mock()),
            ):
                async with app.lifespan(app.app):
                    self.assertEqual(app.app.state.terminal_full_ready, not fail)
                    self.assertTrue(app.app.state.terminal_portfolio_ready)
            self.assertEqual(events[-2:], ["saver-close", "store-close"])
            repository.close.assert_awaited_once()
            engine.close.assert_awaited_once()

    async def test_cancelled_research_startup_closes_optional_resources(self):
        import asyncio
        import app.bootstrap.application as app

        entered = asyncio.Event()
        events = []

        async def wait():
            entered.set()
            await asyncio.Event().wait()

        store = Mock(setup=AsyncMock(side_effect=wait))
        saver = Mock()
        engine = Mock(close=AsyncMock())
        repository = Mock(setup=AsyncMock(), close=AsyncMock())

        def resource(name, value):
            @asynccontextmanager
            async def context(*args):
                try:
                    yield value
                finally:
                    events.append(name)

            return context

        with (
            patch.dict("os.environ", {"RESEARCH_ENABLED": "true"}),
            patch.object(app.AsyncSqliteStore, "from_conn_string", resource("store", store)),
            patch.object(app.AsyncSqliteSaver, "from_conn_string", resource("saver", saver)),
            patch.object(app, "prepare_research_path", AsyncMock(return_value="fixture.sqlite3")),
            patch.object(app, "configure_sqlite", AsyncMock()),
            patch.object(app, "NativeEngine", return_value=engine),
            patch.object(app, "SQLitePortfolioRepository", return_value=repository),
        ):

            async def start():
                async with app.lifespan(app.app):
                    self.fail("Cancelled initialization must not start the backend")

            task = asyncio.create_task(start())
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(events, ["saver", "store"])
        engine.close.assert_awaited_once()
        repository.close.assert_awaited_once()
