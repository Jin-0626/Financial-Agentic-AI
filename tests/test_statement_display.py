import json
import unittest
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from app.statements import financial_statements
from app import routes

class StatementTests(unittest.TestCase):
    def payload(self):
        return {"symbol": "TEST.KL", "income_statement": {"2024-12-31": {"Revenue": 0}, "2025-12-31": {"Revenue": 100, "Net Income": 20}}, "balance_sheet": {}, "cash_flow": {}}
    def test_only_current_turn_and_saved_answer_evidence(self):
        tool = ToolMessage(name="market_data", tool_call_id="data", content=json.dumps(self.payload()))
        messages = [HumanMessage(content="First"), tool, AIMessage(content="Analysis"), HumanMessage(content="Second"), AIMessage(content="No data")]
        self.assertEqual(financial_statements(messages), [])
        history = routes._get_messages_from_state({"messages": messages})
        self.assertEqual(history[1].financial_statements[0]["symbol"], "TEST.KL")
        self.assertEqual(history[-1].financial_statements, [])
