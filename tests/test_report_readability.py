import json
import unittest
from langchain_core.messages import AIMessage, HumanMessage
from app import routes
from app.research_output import format_result, readable_text

class ReportReadabilityTests(unittest.TestCase):
    def test_plain_markdown_is_preserved(self):
        text = "## Executive Summary\n\nRevenue rose.\n\n| Period | Revenue |\n| --- | --- |\n| 2025 | MYR 10m |"
        self.assertEqual(readable_text(text), text)

    def test_internal_receipts_are_not_rendered_as_report(self):
        raw = json.dumps({"kind": "research_evidence_v1", "summary": "Evidence-backed findings.", "evidence": [{"content": "INTERNAL_RECEIPT"}]})
        state = {"messages": [HumanMessage(content="Research"), AIMessage(content=raw)]}
        self.assertEqual(format_result(state, "t")["result"], "Evidence-backed findings.")
        self.assertEqual(routes._get_messages_from_state(state)[-1].content, "Evidence-backed findings.")
        self.assertIn("INTERNAL_RECEIPT", state["messages"][-1].content)

    def test_financial_wrapper_and_legacy_report_become_readable(self):
        self.assertEqual(readable_text(json.dumps({"success": True, "result": "## Analysis\n\nFindings", "todos": []})), "## Analysis\n\nFindings")
        result = readable_text(json.dumps({"executive_summary": "Summary", "key_findings": [{"statement": "Finding"}], "risks": ["Risk"], "recommendations": ["Conditional action"]}))
        for section in ("Executive Summary", "Analysis", "Key Risks", "Recommendations"):
            self.assertIn("## " + section, result)
        self.assertNotIn("executive_summary", result)

    def test_content_blocks_and_reasoning_do_not_become_python_repr(self):
        message = AIMessage(content=[{"type": "text", "text": "<think>private</think>"}, {"type": "text", "text": "## Findings\n\nReadable answer"}])
        text, reasoning = routes._extract_reasoning_and_content(message)
        self.assertEqual(text, "## Findings\n\nReadable answer")
        self.assertEqual(reasoning, "private")

    def test_unknown_json_is_not_reinterpreted_as_a_financial_report(self):
        raw = '{"example": 1}'
        self.assertEqual(readable_text(raw), raw)

    def test_unfinished_tool_call_does_not_return_a_progress_note(self):
        state = {"messages": [HumanMessage(content="Research"), AIMessage(content="I will gather evidence."),
                 AIMessage(content="", tool_calls=[{"name": "market_data", "args": {}, "id": "pending"}])]}
        self.assertFalse(format_result(state, "t")["success"])
        self.assertEqual(format_result(state, "t")["result"], "")

    def test_empty_final_message_does_not_return_a_progress_note(self):
        state = {"messages": [HumanMessage(content="Research"), AIMessage(content="Working..."), AIMessage(content="<think>unfinished</think>")]}
        self.assertFalse(format_result(state, "t")["success"])

    def test_dictionary_history_uses_same_readable_format_and_filters_tools(self):
        state = {"messages": [
            {"role": "assistant", "content": "Internal", "tool_calls": [{"name": "task"}]},
            {"role": "tool", "content": "Private receipt"},
            {"role": "assistant", "content": json.dumps({"success": True, "result": "## Analysis\n\nReadable"})}]}
        history = routes._get_messages_from_state(state)
        self.assertEqual([m.content for m in history], ["## Analysis\n\nReadable"])
