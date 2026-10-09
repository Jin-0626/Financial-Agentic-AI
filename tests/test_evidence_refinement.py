import json, unittest
from langchain_core.messages import ToolMessage, HumanMessage
from app.evidence import receipt, unwrap, pointer
from app.research_tools import current_evidence


class ReceiptIntegrityTests(unittest.TestCase):
    def evidence(self):
        data = {
            "symbol": "AAPL",
            "price": "0.00000001234567",
            "currency": "USD",
            "income": {"2025": {"revenue": 0}},
        }
        meta = receipt("market_data", "q", {"symbol": "AAPL"}, data)
        return ToolMessage(
            name="market_data",
            tool_call_id="q",
            content=json.dumps({"_evidence": meta, "data": data}),
            artifact={"research_evidence": meta},
        )

    def test_precision_zero_currency_and_pointer(self):
        data, meta = unwrap(self.evidence())
        self.assertEqual(pointer(data, "/price"), "0.00000001234567")
        self.assertEqual(pointer(data, "/income/2025/revenue"), 0)
        self.assertEqual(data["currency"], "USD")
        with self.assertRaises(ValueError):
            pointer(data, "/missing")

    def test_content_receipt_tool_and_call_tampering_rejected(self):
        message = self.evidence()
        for updates in (
            {"content": message.content.replace("USD", "MYR")},
            {"name": "financial_news"},
            {"tool_call_id": "other"},
        ):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                unwrap(message.model_copy(update=updates))
        meta = {**message.artifact["research_evidence"], "id": "ev_fake"}
        with self.assertRaises(ValueError):
            unwrap(message.model_copy(update={"artifact": {"research_evidence": meta}}))

    def test_old_turn_is_excluded(self):
        self.assertEqual(
            current_evidence(
                [
                    HumanMessage(content="old"),
                    self.evidence(),
                    HumanMessage(content="new"),
                ]
            ),
            [],
        )

    def test_native_receipts_survive_specialist_boundary(self):
        message = self.evidence().model_copy(
            update={"name": "calculate_historical_var"}
        )
        data = {
            "status": "success",
            "data": {"var": 0.1},
            "provenance": {"currency": "USD"},
        }
        meta = receipt(message.name, "q", {"ticker": "AAPL"}, data)
        message = message.model_copy(
            update={
                "content": json.dumps({"_evidence": meta, "data": data}),
                "artifact": {"research_evidence": meta},
            }
        )
        task = ToolMessage(
            name="task",
            tool_call_id="task",
            content="Risk estimate",
            artifact={
                "specialist_evidence": {
                    "kind": "research_evidence_v1",
                    "summary": "Risk estimate",
                    "evidence": [
                        {
                            "tool": message.name,
                            "call_id": "q",
                            "content": message.content,
                            "artifact": message.artifact,
                        }
                    ],
                }
            },
        )
        found = current_evidence([HumanMessage(content="risk"), task])
        self.assertEqual(unwrap(found[0])[0], data)
