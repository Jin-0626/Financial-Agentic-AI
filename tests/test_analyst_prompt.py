import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from pydantic import ValidationError

from app.prompts import FINANCIAL_ANALYST_PROMPT, SPECIALIST_RESEARCH_PROMPT
from app.subagents import get_subagents_for_type
from research_schema import AnalysisReport, INFORMATIONAL_CAVEAT, render_report
from test_structured_research import ToolModel, report_data


class AnalystPromptTests(unittest.TestCase):
    def test_parent_has_auditable_workflow_and_conditional_verdict(self):
        for instruction in ("write_todos", "data hygiene", "successfully executed code", "Bursa Malaysia", "financial_model.py", "Base, bull", "insufficient evidence", "isolated context"):
            self.assertIn(instruction, FINANCIAL_ANALYST_PROMPT)

    def test_all_specialists_receive_research_execution_standards(self):
        agents = get_subagents_for_type("general")
        self.assertEqual(len(agents), 8)
        for agent in agents:
            self.assertIn(SPECIALIST_RESEARCH_PROMPT, agent["system_prompt"])
            self.assertEqual(agent["skills"], ["/skills/"])
            self.assertNotIn("tools", agent)

    def test_application_factory_really_exposes_planning_tool(self):
        import app.agent as module
        todos = [{"content": "Acquire and validate data", "status": "pending"}]
        done = [{"content": "Acquire and validate data", "status": "completed"}]
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "write_todos", "args": {"todos": todos}, "id": "plan"}]),
            AIMessage(content="", tool_calls=[{"name": "write_todos", "args": {"todos": done}, "id": "update"}]),
            AIMessage(content="", tool_calls=[{"name": "AnalysisReport", "args": report_data(), "id": "report"}]),
        ])
        with patch.object(module, "MODEL_NAME", model), patch.object(module, "get_sandbox_manager", return_value=None):
            graph = module.create_agent(InMemorySaver(), InMemoryStore())
            result = graph.invoke({"messages": [{"role": "user", "content": "Plan a research task"}]}, config={"configurable": {"thread_id": "planning"}})
        self.assertEqual(result["todos"], done)
        self.assertIsInstance(result["structured_response"], AnalysisReport)

    def test_renderer_preserves_large_and_small_number_precision(self):
        data = report_data()
        data["metrics"][0]["value"] = 307645120.123456
        data["metrics"].append({"name": "Small value", "value": 0.00000001234567, "unit": "MYR", "source_ids": ["quote"]})
        text = render_report(AnalysisReport.model_validate(data))
        self.assertIn("307,645,120.123456", text)
        self.assertIn("0.00000001234567", text)
        self.assertNotIn("e+", text)

    def test_metric_date_falls_back_to_actual_source_date(self):
        text = render_report(AnalysisReport.model_validate(report_data()))
        self.assertIn("| Share price | 0.51 | MYR | 2026-10-05 | [quote] |", text)
        self.assertIn("### Financial Metrics\n\n| Metric", text)
        self.assertIn("### Executive Summary\n\n", text)

    def test_financial_and_valuation_findings_render_in_relevant_sections(self):
        data = report_data()
        data["key_findings"] = [
            {"category": "accounting_quality", "statement": "Review the reported working-capital movement.", "source_ids": ["quote"]},
            {"category": "sensitivity", "statement": "Only the supplied scenario assumptions are available.", "source_ids": ["quote"]},
        ]
        text = render_report(AnalysisReport.model_validate(data))
        self.assertIn("### Financial Performance and Accounting Quality\n\n- Review", text)
        self.assertIn("### Valuation and Scenario Analysis\n\n- Only", text)
        self.assertEqual(text.count("Review the reported"), 1)

    def test_raw_provider_reference_is_retained_in_data_but_not_dumped_in_reply(self):
        data = report_data()
        data["sources"][0]["reference"] = '{"symbol":"0157.KL","price":0.51}'
        report = AnalysisReport.model_validate(data)
        self.assertNotIn(data["sources"][0]["reference"], report.answer)
        self.assertEqual(report.model_dump()["sources"][0]["reference"], data["sources"][0]["reference"])

    def test_caveat_is_present_without_fabricating_risks(self):
        data = report_data()
        data.update(status="unavailable", confidence="low", metrics=[], sources=[], data_gaps=["No financial evidence"])
        text = render_report(AnalysisReport.model_validate(data))
        self.assertTrue(text.endswith(INFORMATIONAL_CAVEAT))
        self.assertIn("does not imply low risk", text)
        self.assertIn("Data coverage:** Unavailable", text)

    def test_history_refreshes_current_report_without_duplicating_old_rendering(self):
        from app import routes
        state = {"structured_response": report_data(), "messages": [HumanMessage(content="Research"), AIMessage(content="Old formatting", additional_kwargs={"validated_research": True})]}
        messages = routes._get_messages_from_state(state)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[-1].content, AnalysisReport.model_validate(report_data()).answer)

    def test_same_report_on_two_turns_does_not_erase_current_answer(self):
        from app import routes
        text = AnalysisReport.model_validate(report_data()).answer
        state = {"structured_response": report_data(), "messages": [HumanMessage(content="First request"), AIMessage(content=text, additional_kwargs={"validated_research": True}), HumanMessage(content="Repeat request")]}
        messages = routes._get_messages_from_state(state)
        self.assertEqual([m.content for m in messages if m.role == "assistant"], [text, text])

    def test_derived_metric_requires_code_evidence(self):
        data = report_data()
        data["metrics"][0]["calculation"] = "Input-based calculation in /workspace/financial_model.py"
        with self.assertRaisesRegex(ValidationError, "executed-code"):
            AnalysisReport.model_validate(data)
        data["sources"].append({"id": "code", "tool": "execute", "reference": "/workspace/financial_model.py"})
        data["metrics"][0]["source_ids"].append("code")
        self.assertEqual(AnalysisReport.model_validate(data).metrics[0].value, 0.51)


if __name__ == "__main__":
    unittest.main()
