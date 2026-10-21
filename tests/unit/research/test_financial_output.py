import app.research.resources as research_resources
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langgraph.types import Interrupt
import app.research.resources as routes_resources
import app.research.messages as routes_messages
import app.research.routes as routes_routes
import app.research.streaming as routes_streaming
import app.research.service as routes_service
from app.research.output import format_result
from app.research.runtime import AgentRuntime
import app.research.history as research_history


def agent(state=None, chunks=None, error=None):
    async def stream(*args, **kwargs):
        if error:
            raise error
        for event in chunks or [("values", state)]:
            yield event

    return Mock(ainvoke=AsyncMock(return_value=state, side_effect=error), astream=stream)


async def events():
    return [
        json.loads(e[6:])
        async for e in routes_streaming._stream_sse("Research", "t", "u", "o")
        if json.loads(e[6:]).get("type") != "budget"
    ]


class FinancialOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_split_think_tags_stream_reasoning_and_preserve_partial_failure(self):
        async def stream(*args, **kwargs):
            for text in ("<thi", "nk>Working ", "through evidence", "</thi", "nk>Partial answer"):
                yield ("messages", (AIMessageChunk(content=text), {}))
            raise RuntimeError("Model disconnected fixture")

        with (
            patch.object(research_resources.resources, "agent", Mock(astream=stream)),
            patch.object(routes_resources, "_ensure_user_habits", AsyncMock()),
        ):
            output = await events()
        self.assertEqual(
            "".join(e["content"] for e in output if e["type"] == "reasoning_token"),
            "Working through evidence",
        )
        self.assertEqual(
            "".join(e["content"] for e in output if e["type"] == "token"), "Partial answer"
        )
        self.assertEqual(output[-1]["type"], "error")
        self.assertNotIn("done", [e["type"] for e in output])

    async def test_json_and_stream_same_final_result(self):
        state = {
            "messages": [
                HumanMessage(content="Question"),
                AIMessage(content="## Answer\n\nReadable report"),
            ],
            "todos": ["Fetch"],
        }
        model = agent(
            state,
            [("messages", (AIMessageChunk(content="Draft"), {})), ("values", state)],
        )
        with (
            patch.object(research_resources.resources, "agent", model),
            patch.object(routes_resources, "_ensure_user_habits", AsyncMock()),
        ):
            result = await routes_service._chat("Research", "t", "u", "o")
            output = await events()
        self.assertEqual(result, format_result(state, "t"))
        self.assertEqual(output[-1]["result"], result["result"])
        self.assertEqual(output[0]["type"], "token")
        self.assertNotIn("report", [e["type"] for e in output])

    async def test_report_layout_matches_json_stream_and_history(self):
        from app.research.output import REPORT_SECTIONS

        state = {
            "messages": [
                HumanMessage(content="Research company"),
                AIMessage(
                    content="### Risks\n\nEvidence is limited.\n\n## Executive Summary\n\nVerified identity only.\n\n## Sources\n\nCompany filing."
                ),
            ]
        }
        with (
            patch.object(research_resources.resources, "agent", agent(state)),
            patch.object(routes_resources, "_ensure_user_habits", AsyncMock()),
        ):
            result = await routes_service._chat("Research company", "t", "u", "o")
            output = await events()
        headings = [line[3:] for line in result["result"].splitlines() if line.startswith("## ")]
        self.assertEqual(headings, list(REPORT_SECTIONS))
        self.assertEqual(output[-1]["result"], result["result"])
        self.assertEqual(
            routes_messages._get_messages_from_state(state)[-1].content, result["result"]
        )

    async def test_failure_and_unfinished_run_never_complete(self):
        incomplete = {
            "messages": [
                HumanMessage(content="New"),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "task", "args": {}, "id": "pending"}],
                ),
            ]
        }
        for model in [agent(error=RuntimeError("Provider failed")), agent(incomplete)]:
            with (
                patch.object(research_resources.resources, "agent", model),
                patch.object(routes_resources, "_ensure_user_habits", AsyncMock()),
            ):
                self.assertFalse((await routes_service._chat("Research", "t", "u", "o"))["success"])
                self.assertEqual([e["type"] for e in await events()], ["error"])

    async def test_ollama_stream_failure_is_explicit_in_json_and_sse(self):
        from ollama import ResponseError

        failure = ResponseError("Internal Server Error (ref: example-reference)")
        with (
            patch.object(research_resources.resources, "agent", agent(error=failure)),
            patch.object(routes_resources, "_ensure_user_habits", AsyncMock()),
        ):
            result = await routes_service._chat("Research", "t", "u", "o")
            output = await events()
        self.assertFalse(result["success"])
        self.assertEqual([event["type"] for event in output], ["error"])
        self.assertEqual(output[0]["message"], result["error"])
        self.assertIn("Ollama", result["error"])
        self.assertIn("Research did not finish", result["error"])
        self.assertIn("example-reference", result["error"])

    async def test_interrupt_is_not_completed_report(self):
        with (
            patch.object(
                research_resources.resources,
                "agent",
                agent({"__interrupt__": [Interrupt(value="decision", id="pause")]}),
            ),
            patch.object(routes_resources, "_ensure_user_habits", AsyncMock()),
        ):
            self.assertEqual([e["type"] for e in await events()], ["interrupted"])

    async def test_cancel_reaches_graph(self):
        cancelled = asyncio.Event()

        async def invoke(*args, **kwargs):
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()

        with (
            patch.object(research_resources.resources, "agent", Mock(ainvoke=invoke)),
            patch.object(routes_resources, "_ensure_user_habits", AsyncMock()),
        ):
            task = asyncio.create_task(routes_service._chat("Research", "t", "u", "o"))
            await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(cancelled.is_set())

    async def test_envelope_and_history_content(self):
        state = {
            "messages": [HumanMessage(content="Question"), AIMessage(content="Answer")],
            "todos": ["Research"],
            "files": {"/notes": {}},
        }
        result = format_result(state, "t")
        self.assertEqual(set(result), {"success", "result", "todos", "files", "thread_id", "error"})
        self.assertEqual(result["todos"][0]["task"], "Research")
        self.assertEqual(routes_messages._get_messages_from_state(state)[-1].content, "Answer")

    async def test_selectors_and_thread_isolation(self):
        self.assertEqual(
            routes_messages._request_schema({"response_schema": "financial"}), "financial"
        )
        self.assertEqual(routes_messages._owned_thread("t", "org", "user"), "org__user__t")
        from fastapi import HTTPException

        with self.assertRaises(HTTPException):
            routes_messages._owned_thread("other__user__t", "org", "user")

    async def test_api_result_matches_stream(self):
        state = {"messages": [AIMessage(content="Final")], "todos": []}
        application = FastAPI()
        application.include_router(routes_routes.router, prefix="/api")
        with (
            patch.object(research_resources.resources, "agent", agent(state)),
            patch.object(research_resources.resources, "checkpointer", AsyncMock()),
            patch.object(routes_resources, "_ensure_user_habits", AsyncMock()),
            TestClient(application) as client,
        ):
            body = client.post("/api/chat", json={"message": "Research", "thread_id": "t"}).json()
            stream = client.post(
                "/api/chat/stream", json={"message": "Research", "thread_id": "t"}
            ).text
        done = [json.loads(line[6:]) for line in stream.splitlines() if line.startswith("data: ")][
            -1
        ]
        self.assertTrue(body["success"])
        self.assertEqual(done["result"], body["result"])


class StreamingLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_closing_client_stream_closes_upstream_graph(self):
        closed = asyncio.Event()

        async def graph(*args, **kwargs):
            try:
                yield ("messages", (AIMessageChunk(content="partial"), {}))
                await asyncio.Event().wait()
            finally:
                closed.set()

        runtime = object.__new__(AgentRuntime)
        runtime.resolve = AsyncMock(return_value=Mock(astream=graph))
        with (
            patch.object(research_resources.resources, "agent", runtime),
            patch.object(routes_resources, "_ensure_user_habits", AsyncMock()),
        ):
            stream = routes_streaming._stream_sse("Research", "t", "u", "o")
            await anext(stream)
            await stream.aclose()
        self.assertTrue(closed.is_set())

    async def test_cancelled_consumer_closes_upstream_graph(self):
        waiting, closed = asyncio.Event(), asyncio.Event()

        async def graph(*args, **kwargs):
            try:
                yield ("messages", (AIMessageChunk(content="partial"), {}))
                waiting.set()
                await asyncio.Event().wait()
            finally:
                closed.set()

        async def consume():
            async for _ in routes_streaming._stream_sse("Research", "t", "u", "o"):
                pass

        runtime = object.__new__(AgentRuntime)
        runtime.resolve = AsyncMock(return_value=Mock(astream=graph))
        with (
            patch.object(research_resources.resources, "agent", runtime),
            patch.object(routes_resources, "_ensure_user_habits", AsyncMock()),
        ):
            task = asyncio.create_task(consume())
            await asyncio.wait_for(waiting.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(closed.is_set())


class HistoryReloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_persisted_current_messages_render_after_reload(self):
        from types import SimpleNamespace

        state = {
            "messages": [
                HumanMessage(content="Research company"),
                AIMessage(content="Verified answer"),
            ]
        }
        runtime = Mock(aget_state=AsyncMock(return_value=SimpleNamespace(values=state)))
        with patch.object(research_resources.resources, "agent", runtime):
            result = await research_history._get_history("org__user__thread")
        self.assertEqual([m.role for m in result.messages], ["user", "assistant"])
        self.assertEqual(result.messages[-1].content, "Verified answer")


class ThinkSplitterTests(unittest.TestCase):
    def test_every_chunk_boundary_and_unclosed_reasoning(self):
        from app.research.protocol import _ThinkStreamSplitter

        text = "Before <THINK>reasoning text</THINK>After"
        for split in range(len(text) + 1):
            content, reasoning = [], []
            splitter = _ThinkStreamSplitter(reasoning.append, content.append)
            splitter.push(text[:split])
            splitter.push(text[split:])
            splitter.flush()
            self.assertEqual("".join(content), "Before After")
            self.assertEqual("".join(reasoning), "reasoning text")
        content, reasoning = [], []
        splitter = _ThinkStreamSplitter(reasoning.append, content.append)
        splitter.push("<think>Still working")
        self.assertEqual("".join(reasoning), "Still working")
        splitter.flush()
        self.assertEqual(content, [])
