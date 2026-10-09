import asyncio, json, unittest
from unittest.mock import AsyncMock, Mock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.types import Interrupt
from app import routes
from app.research_output import format_result, error_result


def agent(state=None, chunks=None, error=None):
    async def stream(*args, **kwargs):
        if error:
            raise error
        for event in chunks or [("values", state)]:
            yield event

    return Mock(
        ainvoke=AsyncMock(return_value=state, side_effect=error), astream=stream
    )


async def events():
    return [
        json.loads(e[6:]) async for e in routes._stream_sse("Research", "t", "u", "o") if json.loads(e[6:]).get("type") != "budget"
    ]


class FinancialOutputTests(unittest.IsolatedAsyncioTestCase):
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
            patch.object(routes, "_agent", model),
            patch.object(routes, "_ensure_user_habits", AsyncMock()),
        ):
            result = await routes._chat("Research", "t", "u", "o")
            output = await events()
        self.assertEqual(result, format_result(state, "t"))
        self.assertEqual(output[-1]["result"], result["result"])
        self.assertEqual(output[0]["type"], "token")
        self.assertNotIn("report", [e["type"] for e in output])

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
                patch.object(routes, "_agent", model),
                patch.object(routes, "_ensure_user_habits", AsyncMock()),
            ):
                self.assertFalse(
                    (await routes._chat("Research", "t", "u", "o"))["success"]
                )
                self.assertEqual([e["type"] for e in await events()], ["error"])

    async def test_interrupt_is_not_completed_report(self):
        with (
            patch.object(
                routes,
                "_agent",
                agent({"__interrupt__": [Interrupt(value="decision", id="pause")]}),
            ),
            patch.object(routes, "_ensure_user_habits", AsyncMock()),
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
            patch.object(routes, "_agent", Mock(ainvoke=invoke)),
            patch.object(routes, "_ensure_user_habits", AsyncMock()),
        ):
            task = asyncio.create_task(routes._chat("Research", "t", "u", "o"))
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
        self.assertEqual(
            set(result), {"success", "result", "todos", "files", "thread_id", "error"}
        )
        self.assertEqual(result["todos"][0]["task"], "Research")
        self.assertEqual(routes._get_messages_from_state(state)[-1].content, "Answer")

    async def test_selectors_and_thread_isolation(self):
        self.assertEqual(
            routes._request_schema({"response_schema": "analysis_report"}), "financial"
        )
        self.assertEqual(routes._owned_thread("t", "org", "user"), "org__user__t")
        from fastapi import HTTPException

        with self.assertRaises(HTTPException):
            routes._owned_thread("other__user__t", "org", "user")

    async def test_api_result_matches_stream(self):
        state = {"messages": [AIMessage(content="Final")], "todos": []}
        application = FastAPI()
        application.include_router(routes.router, prefix="/api")
        with (
            patch.object(routes, "_agent", agent(state)),
            patch.object(routes, "_ensure_user_habits", AsyncMock()),
            TestClient(application) as client,
        ):
            body = client.post(
                "/api/chat", json={"message": "Research", "thread_id": "t"}
            ).json()
            stream = client.post(
                "/api/chat/stream", json={"message": "Research", "thread_id": "t"}
            ).text
        done = [
            json.loads(line[6:])
            for line in stream.splitlines()
            if line.startswith("data: ")
        ][-1]
        self.assertTrue(body["success"])
        self.assertEqual(done["result"], body["result"])
