import asyncio
import importlib
import json
import os
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from pydantic import ValidationError

from app import routes
from app.models import AnalysisReport


def report_data():
    return {
        "status": "success", "confidence": "medium", "risks": [], "recommendations": [], "executive_summary": "The retrieved quote is RM 0.51 [quote].",
        "subject": "0157.KL", "metrics": [{
            "name": "Share price", "value": 0.51, "unit": "MYR", "period": None,
            "source_ids": ["quote"], "calculation": None,
        }], "key_findings": [], "sources": [{
            "id": "quote", "tool": "market_data", "reference": "0157.KL quote.price",
            "as_of": "2026-10-05", "timestamp": None,
        }], "data_gaps": [], "tool_errors": [],
    }


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class StructuredResearchTests(unittest.TestCase):
    def test_schema_preserves_evidence_and_missing_values(self):
        data = report_data()
        data["status"] = "partial_success"
        data["data_gaps"] = ["EBITDA unavailable"]
        data["metrics"].append({"name": "EBITDA", "value": None, "unit": None,
                                "period": None, "source_ids": [], "calculation": None})
        result = AnalysisReport.model_validate(data)
        self.assertIsNone(result.metrics[-1].value)
        self.assertEqual(result.model_dump(mode="json")["metrics"][0]["value"], 0.51)

    def test_invalid_or_unreferenced_metrics_rejected(self):
        for change in ("missing_source", "unknown_source", "nan", "infinity", "duplicate_source", "extra_field", "boolean", "numeric_string"):
            with self.subTest(change=change):
                data = report_data()
                if change == "missing_source": data["metrics"][0]["source_ids"] = []
                if change == "unknown_source": data["metrics"][0]["source_ids"] = ["invented"]
                if change == "boolean": data["metrics"][0]["value"] = True
                if change == "numeric_string": data["metrics"][0]["value"] = "0.51"
                if change == "nan": data["metrics"][0]["value"] = float("nan")
                if change == "infinity": data["metrics"][0]["value"] = float("inf")
                if change == "duplicate_source": data["sources"].append(data["sources"][0].copy())
                if change == "extra_field": data["invented"] = True
                with self.assertRaises(ValidationError): AnalysisReport.model_validate(data)

    def test_failures_cannot_be_success_and_unavailable_cannot_have_claims(self):
        data = report_data()
        data["tool_errors"] = [{"tool": "financial_news", "category": "provider", "error": "HTTP 429"}]
        with self.assertRaises(ValidationError): AnalysisReport.model_validate(data)
        data["status"] = "unavailable"
        with self.assertRaises(ValidationError): AnalysisReport.model_validate(data)
        data["metrics"] = []
        self.assertEqual(AnalysisReport.model_validate(data).status, "unavailable")

    def test_factory_uses_framework_structured_output(self):
        module = importlib.import_module("app.agent")
        with patch.object(module, "get_sandbox_manager", return_value=None), patch.object(module, "create_deep_agent") as build:
            module.create_agent(Mock(), Mock())
        strategy = build.call_args.kwargs["response_format"]
        self.assertIsInstance(strategy, ToolStrategy)
        self.assertIs(strategy.schema, AnalysisReport)
        self.assertEqual(len(build.call_args.kwargs["subagents"]), 8)

    def test_real_graph_validates_and_retries_invalid_schema(self):
        from deepagents import create_deep_agent
        from deepagents.backends import StateBackend
        invalid = report_data()
        invalid["metrics"][0]["source_ids"] = []
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "AnalysisReport", "args": invalid, "id": "invalid"}]),
            AIMessage(content="", tool_calls=[{"name": "AnalysisReport", "args": report_data(), "id": "valid"}]),
        ])
        graph = create_deep_agent(model=model, response_format=ToolStrategy(AnalysisReport), backend=StateBackend())
        result = graph.invoke({"messages": [{"role": "user", "content": "Research 0157.KL"}]})
        self.assertIsInstance(result["structured_response"], AnalysisReport)
        self.assertEqual(result["structured_response"].metrics[0].value, 0.51)
        self.assertTrue(any(isinstance(m, ToolMessage) and "Error" in m.content for m in result["messages"]))

    def test_real_graph_retries_free_text_completion(self):
        from deepagents import create_deep_agent
        from deepagents.backends import StateBackend
        from app.research_output import RequireResearchOutput
        model = ToolModel(responses=[
            AIMessage(content="Free text alone"),
            AIMessage(content="", tool_calls=[{"name": "AnalysisReport", "args": report_data(), "id": "valid"}]),
        ])
        graph = create_deep_agent(model=model, response_format=ToolStrategy(AnalysisReport), middleware=[RequireResearchOutput()], backend=StateBackend())
        result = graph.invoke({"messages": [{"role": "user", "content": "Research"}]})
        self.assertEqual(result["structured_response"].answer, AnalysisReport.model_validate(report_data()).answer)
        self.assertFalse(any("Output validation feedback" in m.content for m in routes._get_messages_from_state(result)))

    def test_free_text_retries_are_bounded(self):
        from deepagents import create_deep_agent
        from deepagents.backends import StateBackend
        from app.research_output import RequireResearchOutput
        model = ToolModel(responses=[AIMessage(content="Free text alone", id=f"free-{i}") for i in range(3)])
        graph = create_deep_agent(model=model, response_format=ToolStrategy(AnalysisReport), middleware=[RequireResearchOutput()], backend=StateBackend())
        with self.assertRaisesRegex(ValueError, "after two completion retries"):
            graph.invoke({"messages": [{"role": "user", "content": "Research"}]})

    def test_invalid_schema_retries_are_also_bounded(self):
        from deepagents import create_deep_agent
        from deepagents.backends import StateBackend
        from app.research_output import RequireResearchOutput
        bad = report_data()
        bad["metrics"][0]["source_ids"] = []
        responses = [AIMessage(content="", id=f"invalid-{i}", tool_calls=[{"name": "AnalysisReport", "args": bad, "id": f"invalid-call-{i}"}]) for i in range(3)]
        graph = create_deep_agent(model=ToolModel(responses=responses), response_format=ToolStrategy(AnalysisReport), middleware=[RequireResearchOutput()], backend=StateBackend())
        with self.assertRaisesRegex(ValueError, "after two completion retries"):
            graph.invoke({"messages": [{"role": "user", "content": "Research"}]})

    def test_completion_retry_budget_resets_between_persisted_turns(self):
        from deepagents import create_deep_agent
        from deepagents.backends import StateBackend
        from langgraph.checkpoint.memory import InMemorySaver
        from app.research_output import RequireResearchOutput
        responses = []
        for turn in range(2):
            responses.extend([AIMessage(content="Free text", id=f"{turn}-{i}") for i in range(2)])
            data = report_data()
            data["executive_summary"] = f"Answer {turn}"
            responses.append(AIMessage(content="", id=f"{turn}-valid", tool_calls=[{"name": "AnalysisReport", "args": data, "id": f"report-{turn}"}]))
        graph = create_deep_agent(model=ToolModel(responses=responses), response_format=ToolStrategy(AnalysisReport), middleware=[RequireResearchOutput()], backend=StateBackend(), checkpointer=InMemorySaver())
        for turn in range(2):
            result = graph.invoke({"messages": [{"role": "user", "content": "Research"}]}, config={"configurable": {"thread_id": "retry-reset"}})
            self.assertEqual(result["structured_response"].executive_summary, f"Answer {turn}")

    def test_async_completion_uses_same_validation_retry(self):
        from deepagents import create_deep_agent
        from deepagents.backends import StateBackend
        from app.research_output import RequireResearchOutput
        model = ToolModel(responses=[AIMessage(content="Free text"), AIMessage(content="", tool_calls=[{"name": "AnalysisReport", "args": report_data(), "id": "valid"}])])
        graph = create_deep_agent(model=model, response_format=ToolStrategy(AnalysisReport), middleware=[RequireResearchOutput()], backend=StateBackend())
        result = asyncio.run(graph.ainvoke({"messages": [{"role": "user", "content": "Research"}]}))
        self.assertEqual(result["structured_response"].subject, "0157.KL")

    def test_chat_and_file_chat_expose_validated_json_and_readable_answer(self):
        agent = Mock()
        agent.invoke.return_value = {"messages": [AIMessage(content=""), ToolMessage(content="ack", tool_call_id="report")], "structured_response": report_data()}
        api = FastAPI()
        api.include_router(routes.router, prefix="/api")
        with patch.object(routes, "_agent", agent), patch.object(routes, "_ensure_user_habits"), patch.object(routes, "get_sandbox_manager", return_value=None):
            with TestClient(api) as client:
                for endpoint in ("/api/chat", "/api/chat-with-file"):
                    response = client.post(endpoint, json={"message": "Research", "org_id": "a"})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["structured_response"], report_data())
                    self.assertEqual(response.json()["reply"], AnalysisReport.model_validate(report_data()).answer)
                    self.assertEqual(response.json()["messages"][0]["content"], AnalysisReport.model_validate(report_data()).answer)

    def test_missing_or_invalid_output_returns_failure(self):
        for raw in (None, {"executive_summary": "unvalidated text"}):
            with self.subTest(raw=raw):
                agent = Mock()
                agent.invoke.return_value = {"messages": [AIMessage(content="Unvalidated claim")], "structured_response": raw}
                with patch.object(routes, "_agent", agent), patch.object(routes, "_ensure_user_habits"), patch.object(routes, "get_sandbox_manager", return_value=None):
                    result = routes._sync_chat("Research", "thread", "user", "org")
                self.assertEqual(result["status"], "error")
                self.assertNotIn("structured_response", result)

    def test_sse_done_includes_validated_output_and_preserves_tools(self):
        agent = Mock()
        agent.stream.return_value = iter([
            ("updates", {"model": {"messages": [AIMessage(content="", tool_calls=[{"name": "market_data", "args": {}, "id": "q"}])]}}),
            ("messages", (AIMessageChunk(content="working"), {})),
            ("values", {"structured_response": report_data()}),
        ])
        with patch.object(routes, "_agent", agent), patch.object(routes, "_ensure_user_habits"), patch.object(routes, "get_sandbox_manager", return_value=None):
            events = [json.loads(e.removeprefix("data: ")) for e in routes._stream_sse("Research", "t", "u", "o")]
        self.assertTrue(any(e["type"] == "tool_call" for e in events))
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["reply"], AnalysisReport.model_validate(report_data()).answer)
        self.assertEqual(next(e for e in events if e["type"] == "report")["structured_response"], report_data())

    def test_sse_missing_output_emits_error_without_done(self):
        agent = Mock()
        agent.stream.return_value = iter([("values", {"messages": []})])
        with patch.object(routes, "_agent", agent), patch.object(routes, "_ensure_user_habits"), patch.object(routes, "get_sandbox_manager", return_value=None):
            events = [json.loads(e.removeprefix("data: ")) for e in routes._stream_sse("Research", "t", "u", "o")]
        self.assertEqual(events[-1]["type"], "error")
        self.assertFalse(any(e["type"] == "done" for e in events))

    def test_history_retains_structured_answers_across_turns(self):
        first, second = report_data(), report_data()
        second["executive_summary"] = "Second answer"
        first_text = AnalysisReport.model_validate(first).answer
        second_text = AnalysisReport.model_validate(second).answer
        history = routes._get_messages_from_state({"messages": [
            AIMessage(content=first_text, additional_kwargs={"validated_research": True}),
            HumanMessage(content="Next request"),
            AIMessage(content=second_text, additional_kwargs={"validated_research": True}),
        ], "structured_response": second})
        self.assertEqual([m.content for m in history if m.role == "assistant"], [first_text, second_text])

    def test_openapi_describes_typed_research_response(self):
        api = FastAPI()
        api.include_router(routes.router, prefix="/api")
        schema = api.openapi()
        self.assertIn("AnalysisReport", schema["components"]["schemas"])
        self.assertEqual(schema["paths"]["/api/chat"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"], "#/components/schemas/ChatResponse")

    def test_json_text_is_not_accepted_as_a_final_report(self):
        from deepagents import create_deep_agent
        from deepagents.backends import StateBackend
        from app.research_output import RequireResearchOutput
        for fenced in (False, True):
            text = json.dumps(report_data())
            if fenced:
                text = "```json" + text + "```"
            responses = [AIMessage(content=text, id=f"text-{i}") for i in range(3)]
            graph = create_deep_agent(model=ToolModel(responses=responses), response_format=ToolStrategy(AnalysisReport), middleware=[RequireResearchOutput()], backend=StateBackend())
            with self.assertRaisesRegex(ValueError, "after two completion retries"):
                graph.invoke({"messages": [{"role": "user", "content": "Research"}]})

    def test_malformed_report_gets_exact_schema_feedback_then_recovers(self):
        from deepagents import create_deep_agent
        from deepagents.backends import StateBackend
        from app.research_output import RequireResearchOutput
        broken = {"executive_summary": "Unverified JSON", "status": "complete", "metrics": [{"metric": "Price", "value": 0.41}], "key_findings": [{"finding": "Unverified"}], "sources": [{"source_type": "market_data"}], "data_gaps": [{"type": "news"}], "tool_errors": [{"tool": "financial_news", "error": "403"}]}
        model = ToolModel(responses=[AIMessage(content=json.dumps(broken)), AIMessage(content="", tool_calls=[{"name": "AnalysisReport", "args": report_data(), "id": "fixed"}])])
        graph = create_deep_agent(model=model, response_format=ToolStrategy(AnalysisReport), middleware=[RequireResearchOutput()], backend=StateBackend())
        result = graph.invoke({"messages": [{"role": "user", "content": "Research"}]})
        feedback = next(m.content for m in result["messages"] if m.additional_kwargs.get("research_output_feedback"))
        self.assertIn("JSON schema:", feedback)
        self.assertIn("Metrics use name, not metric", feedback)
        self.assertIn("partial_success", feedback)
        self.assertEqual(result["structured_response"].metrics[0].value, 0.51)
        self.assertFalse(any(m.content == json.dumps(broken) for m in routes._get_messages_from_state(result)))

    def test_nullable_unknown_fields_default_to_null_without_losing_validation(self):
        data = report_data()
        data["sources"][0].pop("as_of")
        for name in ("unit", "period", "calculation"): data["metrics"][0].pop(name)
        report = AnalysisReport.model_validate(data)
        self.assertIsNone(report.sources[0].as_of)
        self.assertIsNone(report.metrics[0].unit)
        data["metrics"][0]["source_ids"] = []
        with self.assertRaises(ValidationError): AnalysisReport.model_validate(data)

    def test_sse_rejected_json_never_appears_as_visible_answer(self):
        agent = Mock()
        broken = json.dumps({"executive_summary": "Rejected private report", "status": "complete"})
        agent.stream.return_value = iter([("messages", (AIMessageChunk(content=broken), {})), ("values", {"messages": []})])
        with patch.object(routes, "_agent", agent), patch.object(routes, "_ensure_user_habits"), patch.object(routes, "get_sandbox_manager", return_value=None):
            events = [json.loads(e.removeprefix("data: ")) for e in routes._stream_sse("Research", "t", "u", "o")]
        self.assertEqual(events[-1]["type"], "error")
        self.assertFalse(any(e["type"] == "token" for e in events))
        self.assertNotIn("Rejected private report", json.dumps(events))

    def test_sse_only_publishes_final_validated_answer_after_retry(self):
        agent = Mock()
        agent.stream.return_value = iter([
            ("messages", (AIMessageChunk(content="Unvalidated draft"), {})),
            ("messages", (ToolMessage(content="Internal schema error", name="AnalysisReport", tool_call_id="bad"), {})),
            ("values", {"structured_response": report_data()}),
        ])
        with patch.object(routes, "_agent", agent), patch.object(routes, "_ensure_user_habits"), patch.object(routes, "get_sandbox_manager", return_value=None):
            events = [json.loads(e.removeprefix("data: ")) for e in routes._stream_sse("Research", "t", "u", "o")]
        self.assertEqual([e["reply"] for e in events if e["type"] == "report"], [AnalysisReport.model_validate(report_data()).answer])
        self.assertNotIn("Internal schema error", json.dumps(events))

    def test_provider_errors_are_sanitized(self):
        data = report_data()
        data["status"] = "partial_success"
        data["tool_errors"] = [{"tool": "financial_news", "category": "provider", "error": "request failed with fixture-private-secret"}]
        with patch.dict(os.environ, {"FMP_API_KEY": "fixture-private-secret"}):
            report = routes._validated_research({"structured_response": data})
        self.assertNotIn("fixture-private-secret", report.tool_errors[0].error)


if __name__ == "__main__":
    unittest.main()
