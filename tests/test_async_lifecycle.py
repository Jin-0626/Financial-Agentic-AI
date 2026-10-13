import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock, patch
from langgraph.store.memory import InMemoryStore
from app.namespace_router import user_namespace
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
        rt = SimpleNamespace(
            context=SimpleNamespace(org_id="org-b", user_id="u"), server_info=None
        )
        self.assertEqual(user_namespace(rt), ("memories", "org-b", "u"))

    async def test_startup_and_failure_close_all_resources(self):
        import app

        for fail in [False, True]:
            events = []
            store = Mock(setup=AsyncMock())
            saver = Mock(
                setup=AsyncMock(
                    side_effect=RuntimeError("fixture failure") if fail else None
                )
            )
            engine = Mock(close=AsyncMock())
            pool = Mock()

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
                patch.object(
                    app.AsyncPostgresStore, "from_conn_string", resource("store", store)
                ),
                patch.object(
                    app.AsyncPostgresSaver, "from_conn_string", resource("saver", saver)
                ),
                patch.object(app, "AsyncConnectionPool", resource("pool", pool)),
                patch.object(app, "NativeEngine", return_value=engine),
                patch.object(
                    app, "PortfolioRepository", return_value=Mock(setup=AsyncMock())
                ),
                patch.object(app, "_ensure_agents_memory", AsyncMock()),
                patch.object(app, "migrate_checkpoint_contract", AsyncMock()),
                patch.object(app, "AgentRuntime", return_value=Mock()),
            ):
                if fail:
                    with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                        async with app.lifespan(app.app):
                            pass
                else:
                    async with app.lifespan(app.app):
                        pass
            self.assertEqual(events[-3:], ["pool-close", "saver-close", "store-close"])
            engine.close.assert_awaited_once()
