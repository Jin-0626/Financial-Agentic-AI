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
        from app.research_output import normalize_report
        self.assertEqual([m.content for m in history], [normalize_report("## Analysis\n\nReadable")])


class FixedResearchStructureTests(unittest.TestCase):
    def test_fixed_order_aliases_duplicates_and_no_lost_content(self):
        from app.research_output import normalize_report, REPORT_SECTIONS
        raw="## Recommendations\n\nWait for verified earnings.\n\n# 2. Risks\n\nLimited evidence.\n\n**Executive Summary**\n\nCompany review.\n\n## Analysis\n\n| Period | Revenue |\n| --- | --- |\n| 2025 | MYR 10m |\n\n## Sources\n\n[Annual report](https://example.com/report)\n\n## Analysis\n\nAdditional finding."
        text=normalize_report(raw)
        headings=[line[3:] for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings,list(REPORT_SECTIONS))
        for content in ("Wait for verified earnings.","Limited evidence.","Company review.","MYR 10m","https://example.com/report","Additional finding."):
            self.assertIn(content,text)
        self.assertEqual(normalize_report(text),text)

    def test_missing_sections_are_disclosed_not_invented(self):
        from app.research_output import normalize_report
        text=normalize_report("```markdown\n## Executive Summary\n\nOnly identity was verified.\n```")
        self.assertEqual(text.count("This section was not provided"),4)
        self.assertNotIn("```",text)
        self.assertNotIn("buy",text)

    def test_json_stream_and_history_share_fixed_structure(self):
        raw=json.dumps({"executive_summary":"Summary","key_findings":["Finding"],"risks":["Risk"],"recommendations":["Wait"],"sources":["Company filing"]})
        state={"messages":[HumanMessage(content="Research"),AIMessage(content=raw)]}
        text=format_result(state,"t")["result"]
        self.assertEqual(routes._get_messages_from_state(state)[-1].content,text)
        self.assertIn("## Sources\n\n- Company filing",text)
        self.assertEqual(state["messages"][-1].content,raw)

    def test_ordinary_chat_and_code_are_preserved(self):
        from app.research_output import normalize_report
        for raw in ("Hello!", "The answer is 42.", "```python\n## Analysis\nprint(1)\n```"):
            self.assertEqual(normalize_report(raw),raw)


    def test_sample_confidence_and_source_links_are_retained(self):
        raw=json.dumps({"executive_summary":"Summary","confidence":"low","sources":[{"title":"Filing","url":"https://example.com/filing"}]})
        text=format_result({"messages":[AIMessage(content=raw)]},"t")["result"]
        self.assertIn("Confidence: low",text)
        self.assertIn("[Filing](<https://example.com/filing>)",text)
