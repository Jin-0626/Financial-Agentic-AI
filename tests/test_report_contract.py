import json
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import ValidationError
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command, Interrupt, interrupt
from langchain_core.tools import tool

from app import routes
from app.context_type import MemoryContext
from app.research_output import RequireResearchOutput, build_response_format
from research_schema import AnalysisReport, render_report
from test_structured_research import ToolModel, report_data


class ReportContractTests(unittest.TestCase):
    def events(self, chunks, **kwargs):
        agent = Mock()
        agent.stream.return_value = iter(chunks)
        with patch.object(routes, "_agent", agent), patch.object(routes, "_ensure_user_habits"), patch.object(routes, "get_sandbox_manager", return_value=None):
            return [json.loads(item.removeprefix("data: ")) for item in routes._stream_sse("Research", "t", "u", "o", **kwargs)]

    def test_invalid_confidence_and_malformed_sections_rejected(self):
        for field, value in (("confidence", "certain"), ("risks", ["unsupported risk"]), ("recommendations", [{"statement": "Buy"}])):
            data = report_data()
            data[field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError):
                AnalysisReport.model_validate(data)

    def test_validation_feedback_identifies_field_without_echoing_private_input(self):
        from langchain.agents.structured_output import StructuredOutputValidationError
        from app.research_output import research_schema_feedback
        data = report_data()
        data["confidence"] = "private-fixture-invalid-value"
        try:
            AnalysisReport.model_validate(data)
        except ValidationError as cause:
            try:
                raise ValueError("Framework parse wrapper") from cause
            except ValueError as wrapped:
                error = StructuredOutputValidationError("AnalysisReport", wrapped, AIMessage(content=""))
                feedback = research_schema_feedback(error)
        self.assertIn('"field": "confidence"', feedback)
        self.assertNotIn("private-fixture-invalid-value", feedback)

    def test_provider_timestamp_date_mismatch_is_rejected(self):
        data = report_data()
        data["sources"][0]["as_of"] = "2026-10-20"
        state = {"structured_response": data, "messages": [ToolMessage(content=json.dumps({"timestamp": 1791190507, "price": 0.505}), name="market_data", tool_call_id="quote")]}
        with self.assertRaisesRegex(ValueError, "2026-10-05"):
            routes._validated_research(state)
        data["sources"][0]["as_of"] = "2026-10-05"
        self.assertEqual(routes._validated_research(state).sources[0].as_of, "2026-10-05")

    def test_retrieved_timestamp_is_preserved_without_model_date_arithmetic(self):
        data = report_data()
        data["sources"][0]["as_of"] = None
        state = {"structured_response": data, "messages": [ToolMessage(content=json.dumps({"timestamp": 1791190507}), name="market_data", tool_call_id="quote")]}
        report = routes._validated_research(state)
        self.assertEqual(report.sources[0].as_of, "2026-10-05")
        self.assertEqual(report.sources[0].timestamp, 1791190507)

    def test_real_graph_corrects_mismatched_provider_date_with_bounded_feedback(self):
        @tool
        def market_data() -> str:
            """Retrieve a dated provider quote."""
            return json.dumps({"timestamp": 1791190507, "price": 0.51})
        wrong = report_data()
        wrong["sources"][0]["as_of"] = "2026-10-20"
        model = ToolModel(responses=[AIMessage(content="", tool_calls=[{"name": "market_data", "args": {}, "id": "quote"}]), AIMessage(content="", tool_calls=[{"name": "AnalysisReport", "args": wrong, "id": "wrong"}]), AIMessage(content="", tool_calls=[{"name": "AnalysisReport", "args": report_data(), "id": "fixed"}])])
        graph = create_deep_agent(model=model, tools=[market_data], response_format=build_response_format(), middleware=[RequireResearchOutput()], backend=StateBackend())
        state = graph.invoke({"messages": [{"role": "user", "content": "Research"}]})
        self.assertEqual(state["structured_response"].sources[0].timestamp, 1791190507)
        self.assertTrue(any("Evidence validation" in message.content for message in state["messages"] if message.additional_kwargs.get("research_output_feedback")))

    def test_strict_checkpoint_serialization_preserves_typed_report(self):
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        serializer = JsonPlusSerializer(allowed_msgpack_modules=[AnalysisReport])
        original = AnalysisReport.model_validate(report_data())
        restored = serializer.loads_typed(serializer.dumps_typed(original))
        self.assertIsInstance(restored, AnalysisReport)
        self.assertEqual(restored.model_dump(mode="json"), original.model_dump(mode="json"))

    def test_unknown_schema_fails_factory_and_api_without_invocation(self):
        with self.assertRaisesRegex(ValueError, "Unsupported response_schema"):
            build_response_format({"response_schema": "unrecognized"})
        self.assertIsNone(build_response_format({"response_schema": None}))
        api = FastAPI()
        api.include_router(routes.router, prefix="/api")
        agent = Mock()
        with patch.object(routes, "_agent", agent), TestClient(api) as client:
            for endpoint in ("/api/chat", "/api/chat-with-file", "/api/chat/stream", "/api/chat-with-file/stream"):
                response = client.post(endpoint, json={"message": "Research", "response_schema": "unknown"})
                self.assertEqual(response.status_code, 400)
        agent.invoke.assert_not_called()
        agent.stream.assert_not_called()

    def test_only_top_level_final_state_can_supply_report(self):
        events = self.events([("updates", {"task": {"structured_response": report_data()}}), ("values", {"messages": [ToolMessage(content=json.dumps(report_data()), name="task", tool_call_id="subagent")]})])
        self.assertEqual(events[-1]["type"], "error")
        self.assertFalse(any(e["type"] in {"report", "done"} for e in events))

    def test_partial_report_produces_exactly_one_final_report_event(self):
        data = report_data()
        data.update(status="partial_success", data_gaps=["News unavailable"])
        events = self.events([("updates", {"model": {"messages": [AIMessage(content="", tool_calls=[{"name": "market_data", "args": {}, "id": "q"}])]}}), ("values", {"structured_response": data})])
        self.assertEqual([e["type"] for e in events], ["tool_call", "report", "done"])
        self.assertEqual(events[1]["structured_response"]["status"], "partial_success")
        self.assertNotIn("structured_response", events[-1])
        self.assertEqual(events[-1]["reply"], render_report(AnalysisReport.model_validate(data)))

    def test_observed_treasury_failure_is_not_hidden_by_report_success(self):
        payload = {"status": "partial_success", "provider_errors": [{"provider": "FMP", "error": "HTTP 403"}], "treasury_yield_proxies": [{"symbol": "^TNX", "price": 4.1}]}
        state = {"messages": [HumanMessage(content="Research"), ToolMessage(content=json.dumps(payload), name="economics_data", tool_call_id="macro")], "structured_response": report_data()}
        report = routes._validated_research(state)
        self.assertEqual(report.status, "partial_success")
        self.assertEqual(report.metrics[0].value, 0.51)
        self.assertIn("HTTP 403", report.answer)

    def test_failed_script_artifact_is_retained_as_execution_failure(self):
        state = {"structured_response": report_data(), "messages": [ToolMessage(content="ModuleNotFoundError: missing package", name="execute", tool_call_id="script", artifact={"exit_code": 1})]}
        report = routes._validated_research(state)
        self.assertEqual(report.status, "partial_success")
        self.assertEqual(report.tool_errors[0].category, "sandbox")
        self.assertIn("missing package", report.answer)

    def test_empty_news_is_gap_not_provider_exception(self):
        state = {"messages": [ToolMessage(content=json.dumps({"status": "empty", "articles": [], "attempts": [{"provider": "Yahoo", "status": "empty"}]}), name="financial_news", tool_call_id="news")], "structured_response": report_data()}
        report = routes._validated_research(state)
        self.assertEqual(report.status, "partial_success")
        self.assertTrue(report.data_gaps)
        self.assertEqual(report.tool_errors, [])

    def test_previous_turn_provider_failure_does_not_contaminate_new_report(self):
        state = {"messages": [ToolMessage(content=json.dumps({"error": "Old failure"}), name="financial_news", tool_call_id="old"), HumanMessage(content="New research")], "structured_response": report_data()}
        self.assertEqual(routes._validated_research(state).status, "success")

    def test_interrupted_stream_has_no_final_report_or_completion(self):
        events = self.events([("values", {"structured_response": report_data(), "__interrupt__": [Interrupt(value={"decision": "approve"}, id="approval")]})])
        self.assertEqual([e["type"] for e in events], ["interrupted"])

    def test_cancelled_generator_clears_org_without_completion(self):
        agent = Mock()
        agent.stream.return_value = iter([("updates", {"model": {"messages": [AIMessage(content="", tool_calls=[{"name": "market_data", "args": {}, "id": "q"}])]}}), ("values", {"structured_response": report_data()})])
        with patch.object(routes, "_agent", agent), patch.object(routes, "_ensure_user_habits"), patch.object(routes, "get_sandbox_manager", return_value=None):
            stream = routes._stream_sse("Research", "t", "u", "o")
            self.assertEqual(json.loads(next(stream).removeprefix("data: "))["type"], "tool_call")
            stream.close()
        self.assertIsNone(routes._get_current_org())

    def test_real_graph_ordinary_chat_disables_report_for_that_turn(self):
        graph = create_deep_agent(model=ToolModel(responses=[AIMessage(content="Hello")]), response_format=build_response_format(), middleware=[RequireResearchOutput()], context_schema=MemoryContext, backend=StateBackend())
        result = graph.invoke({"messages": [{"role": "user", "content": "Hello"}]}, context=MemoryContext(response_schema=None))
        self.assertIsNone(result.get("structured_response"))
        self.assertEqual(result["messages"][-1].content, "Hello")

    def test_real_graph_interrupt_resumes_to_validated_report(self):
        @tool
        def decision() -> str:
            """Ask for a decision before continuing."""
            return str(interrupt({"decision": "approve"}))
        model = ToolModel(responses=[AIMessage(content="", tool_calls=[{"name": "decision", "args": {}, "id": "pause"}]), AIMessage(content="", tool_calls=[{"name": "AnalysisReport", "args": report_data(), "id": "report"}])])
        graph = create_deep_agent(model=model, tools=[decision], response_format=build_response_format(), middleware=[RequireResearchOutput()], backend=StateBackend(), checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "resume"}}
        paused = graph.invoke({"messages": [{"role": "user", "content": "Research"}]}, config=config)
        self.assertTrue(paused["__interrupt__"])
        self.assertIsNone(paused.get("structured_response"))
        resumed = graph.invoke(Command(resume="approved"), config=config)
        self.assertEqual(AnalysisReport.model_validate(resumed["structured_response"]).metrics[0].value, 0.51)
        self.assertEqual(resumed["messages"][-1].content, render_report(AnalysisReport.model_validate(report_data())))

    def test_separate_fastapi_startup_initializes_real_agent_contract(self):
        import app as backend
        import app.agent as factory
        @contextmanager
        def store_context(*args, **kwargs):
            yield InMemoryStore()
        @contextmanager
        def saver_context(*args, **kwargs):
            yield InMemorySaver()
        model = ToolModel(responses=[AIMessage(content="", tool_calls=[{"name": "AnalysisReport", "args": report_data(), "id": "report"}])])
        with patch("langgraph.store.postgres.PostgresStore.from_conn_string", side_effect=store_context), patch("langgraph.checkpoint.postgres.PostgresSaver.from_conn_string", side_effect=saver_context), patch.object(InMemoryStore, "setup", create=True), patch.object(InMemorySaver, "setup", create=True), patch.object(backend, "init_sandbox_manager", return_value=None), patch.object(factory, "get_sandbox_manager", return_value=None), patch.object(factory, "MODEL_NAME", model), patch.object(backend, "_ensure_agents_memory"), patch.object(backend, "_remove_per_user_agents_memory"):
            with TestClient(backend.app) as client:
                response = client.post("/api/chat", json={"message": "Research", "thread_id": "org__user__test"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["structured_response"]["confidence"], "medium")
                self.assertIn("### Key Findings", response.json()["reply"])
        self.assertIsNone(backend._agent)


if __name__ == "__main__":
    unittest.main()
