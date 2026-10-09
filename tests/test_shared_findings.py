import unittest
from dataclasses import dataclass, replace
from types import SimpleNamespace
from langchain_core.messages import HumanMessage, ToolMessage
from app.research_tools import ResearchTools

@dataclass
class Request:
    tool_call: dict
    runtime: object
    def override(self, **kwargs):
        return replace(self, **kwargs)

class SharedFindingsTests(unittest.TestCase):
    def test_only_current_successful_summaries_pass_with_exact_values(self):
        def result(text, status="success"):
            return ToolMessage(name="task", content=text, tool_call_id=text,
                status=status, artifact={"specialist_evidence": {"summary": text, "evidence": ["PRIVATE RECEIPT"]}})
        messages = [result("Old findings"), HumanMessage(content="Current request"),
                    result("MYR 2.69 billion; Q2 2026; source: Yahoo Finance"), result("Failed", "error")]
        request = Request({"name": "task", "args": {"description": "Interpret ratios", "subagent_type": "data-analyst"}}, SimpleNamespace(state={"messages": messages}))
        changed = ResearchTools.shared_assignment(request)
        text = changed.tool_call["args"]["description"]
        self.assertIn("MYR 2.69 billion; Q2 2026; source: Yahoo Finance", text)
        for excluded in ("Old findings", "Failed", "PRIVATE RECEIPT"):
            self.assertNotIn(excluded, text)
        self.assertEqual(request.tool_call["args"]["description"], "Interpret ratios")
        self.assertEqual(changed.tool_call["args"]["subagent_type"], "data-analyst")

    def test_whole_summary_bounds_and_non_task_calls(self):
        large = "x" * 12001
        message = ToolMessage(name="task", content=large, tool_call_id="large", artifact={"specialist_evidence": {"summary": large}})
        request = Request({"name": "task", "args": {"description": "Analyze"}}, SimpleNamespace(state={"messages": [message]}))
        self.assertIs(ResearchTools.shared_assignment(request), request)
        request.tool_call["name"] = "market_data"
        self.assertIs(ResearchTools.shared_assignment(request), request)

    def test_public_data_transfers_with_integrity_and_private_data_excluded(self):
        import json
        from app.evidence import receipt
        data = {"symbol": "1155.KL", "currency": "MYR", "as_of": "2026-06-30", "net_income": 2690000000}
        meta = receipt("market_data", "provider", {"symbol": "1155.KL"}, data)
        row = {"tool": "market_data", "call_id": "provider", "status": "success",
               "content": json.dumps(data), "artifact": {"research_evidence": meta}}
        private_meta = receipt("portfolio_analytics", "private", {}, {"private_position": 99})
        private = {"tool": "portfolio_analytics", "call_id": "private", "status": "success",
                   "content": json.dumps({"private_position": 99}), "artifact": {"research_evidence": private_meta}}
        result = ToolMessage(name="task", content="Findings", tool_call_id="task",
                artifact={"specialist_evidence": {"kind": "research_evidence_v1", "summary": "Quarterly findings", "evidence": [row, private]}})
        request = Request({"name": "task", "args": {"description": "Analyze figures"}},
                          SimpleNamespace(state={"messages": [HumanMessage(content="Research"), result]}))
        text = ResearchTools.shared_assignment(request).tool_call["args"]["description"]
        self.assertIn('"net_income": 2690000000', text)
        self.assertIn(meta["id"], text)
        self.assertNotIn("private_position", text)
        row["content"] = json.dumps({**data, "net_income": 1})
        text = ResearchTools.shared_assignment(request).tool_call["args"]["description"]
        self.assertNotIn("Verified public provider data", text)
