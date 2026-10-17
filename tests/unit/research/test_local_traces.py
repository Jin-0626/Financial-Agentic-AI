import app.research.resources as research_resources
import asyncio
import json
import unittest
from uuid import uuid4
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

import app.research.routes as routes_routes
import app.research.resources as routes_resources
import app.research.service as routes_service
from app.infrastructure.auth import Identity, authenticated_identity
from app.infrastructure.traces import TraceStore, payload, MAX_PAYLOAD, MAX_TRACES, MAX_CALLS


class LocalTraceTests(unittest.TestCase):
    def test_nested_calls_payload_duration_and_errors(self):
        store = TraceStore()
        recorder = store.begin("o", "u", "thread", "Research")
        parent, child = uuid4(), uuid4()
        recorder.on_chain_start(None, {}, run_id=parent, name="research")
        recorder.on_tool_start(
            {"name": "quote"}, '{"symbol":"5326.KL"}', run_id=child, parent_run_id=parent
        )
        recorder.on_tool_end(
            ToolMessage(content="provider failed", status="error", tool_call_id="t"), run_id=child
        )
        recorder.on_chain_error(RuntimeError("failure"), run_id=parent)
        recorder.finish("error", RuntimeError("failure"))
        row = store.read("o", "u", recorder.trace_id)[0]
        self.assertEqual(row["calls"][1]["parent_id"], str(parent))
        self.assertEqual(row["calls"][1]["input"], {"symbol": "5326.KL"})
        self.assertEqual(row["calls"][1]["output"]["content"], "provider failed")
        self.assertEqual(row["calls"][1]["status"], "error")
        self.assertGreater(row["calls"][1]["duration_ms"], 0)
        self.assertEqual(row["status"], "error")
        row["calls"].clear()
        self.assertEqual(len(store.read("o", "u", recorder.trace_id)[0]["calls"]), 2)

    def test_redaction_and_payload_limits(self):
        with patch.dict("os.environ", {"PROVIDER_API_KEY": "super-private-value"}):
            safe = payload(
                {
                    "api_key": "another-secret",
                    "nested": {"Authorization": "Bearer private"},
                    "json_content": '{"api_key": "json-secret", "token_usage": 42}',
                    "content": "super-private-value https://user:pass@example.com",
                }
            )
        self.assertNotIn("json-secret", json.dumps(safe))
        self.assertEqual(safe["json_content"]["token_usage"], 42)
        self.assertNotIn("another-secret", json.dumps(safe))
        self.assertNotIn("Bearer private", json.dumps(safe))
        self.assertNotIn("super-private-value", json.dumps(safe))
        self.assertNotIn("user:pass", json.dumps(safe))
        self.assertTrue(payload("x" * (MAX_PAYLOAD * 2))["truncated"])
        self.assertTrue(payload("漢" * MAX_PAYLOAD)["truncated"])
        self.assertEqual(payload("first line\nsecond line"), "first line\nsecond line")

    def test_retention_running_limits_and_cancellation(self):
        store = TraceStore()
        for _ in range(MAX_TRACES):
            store.begin("o", "u", "t", "Research").finish()
        recorder = store.begin("o", "u", "t", "Latest")
        self.assertEqual(len(store.read("o", "u")), MAX_TRACES)
        for _ in range(MAX_CALLS + 1):
            recorder.on_tool_start({"name": "quote"}, "{}", run_id=uuid4())
        recorder.finish("interrupted")
        row = store.read("o", "u", recorder.trace_id)[0]
        self.assertTrue(row["limited"])
        self.assertEqual(len(row["calls"]), MAX_CALLS)
        self.assertTrue(all(call["status"] == "interrupted" for call in row["calls"]))
        self.assertEqual(store.read("other", "u"), [])

    def test_trace_endpoints_are_workspace_scoped(self):
        store = TraceStore()
        recorder = store.begin("o", "u", "t", "Private research")
        app = FastAPI()
        app.include_router(routes_routes.router)
        with patch.object(routes_resources, "traces", store), TestClient(app) as client:
            response = client.get("/traces?org_id=o&user_id=u")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(len(response.json()["traces"]), 1)
            self.assertEqual(
                client.get(f"/traces/{recorder.trace_id}?org_id=o&user_id=u").status_code, 200
            )
            self.assertEqual(
                client.get(f"/traces/{recorder.trace_id}?org_id=o&user_id=other").status_code, 404
            )
            self.assertEqual(client.get("/traces?org_id=other&user_id=u").json()["traces"], [])
            app.dependency_overrides[authenticated_identity] = lambda: Identity("o", "u")
            self.assertEqual(
                len(client.get("/traces?org_id=spoofed&user_id=spoofed").json()["traces"]), 1
            )

    def test_real_agent_propagates_tool_callbacks(self):
        @tool
        def quote(symbol: str) -> dict:
            """Retrieve a deterministic quote."""
            return {"symbol": symbol, "price": "10.00"}

        class ToolModel(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self

        model = ToolModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "quote", "args": {"symbol": "5326.KL"}, "id": "q"}],
                ),
                AIMessage(content="Complete"),
            ]
        )
        graph = create_agent(model, tools=[quote])
        store = TraceStore()
        recorder = store.begin("o", "u", "t", "Research")
        graph.invoke(
            {"messages": [{"role": "user", "content": "Research"}]}, {"callbacks": [recorder]}
        )
        recorder.finish()
        row = store.read("o", "u", recorder.trace_id)[0]
        tools = [call for call in row["calls"] if call["kind"] == "tool"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["input"]["symbol"], "5326.KL")
        self.assertIn("10.00", str(tools[0]["output"]))
        self.assertEqual(tools[0]["status"], "success")
        self.assertEqual(len([call for call in row["calls"] if call["kind"] == "model"]), 2)


class TraceLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_exception_and_cancellation_close_traces(self):
        store = TraceStore()

        async def cancelled(*args, **kwargs):
            raise asyncio.CancelledError()

        for invoke, status in [
            (AsyncMock(side_effect=RuntimeError("model failed")), "error"),
            (cancelled, "interrupted"),
        ]:
            with (
                patch.object(routes_resources, "traces", store),
                patch.object(routes_resources, "_ensure_user_habits", AsyncMock()),
                patch.object(research_resources.resources, "agent", Mock(ainvoke=invoke)),
            ):
                try:
                    await routes_service._chat("Research", "t", "u", "o")
                except asyncio.CancelledError:
                    pass
            self.assertEqual(store.read("o", "u")[0]["status"], status)
