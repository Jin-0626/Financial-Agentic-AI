import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from langchain_core.messages import ToolMessage, HumanMessage
from langchain.agents.middleware.types import ModelRequest
from agent_fixtures import ToolModel
from orchestrator.budgets import RunBudget, ExecutionLimits
from app.research_tools import ResearchTools
from app.context_type import MemoryContext
from app.evidence import unwrap

class BudgetCompletionTests(unittest.TestCase):
    def test_atomic_reservations_and_synthesis_tools(self):
        budget = RunBudget(max_tokens=1000000)
        self.assertTrue(budget.reserve_model(850000, "specialist"))
        self.assertFalse(budget.reserve_model(60000, "specialist"))
        self.assertEqual(budget.phase, "synthesis")
        budget.release(850000)
        request = ModelRequest(model=ToolModel(responses=[]), messages=[HumanMessage(content="Research")], tools=[], state={})
        bounded = ExecutionLimits().bounded_request(request, budget)
        self.assertEqual(bounded.tools, [])
        self.assertIn("Produce the final report now", bounded.system_message.text)
        self.assertEqual(budget.snapshot()["synthesis_reserve"], 100000)

    def test_concurrent_provider_reuse_has_valid_receipts(self):
        async def run():
            from dataclasses import dataclass, replace
            @dataclass
            class Request:
                tool_call: dict
                runtime: object
                tool: object = None
                def override(self, **kwargs): return replace(self, **kwargs)
            context = MemoryContext()
            runtime = SimpleNamespace(context=context, state={})
            first = Request({"name": "market_data", "id": "a", "args": {"symbol": "AAPL"}}, runtime)
            second = first.override(tool_call={**first.tool_call, "id": "b"})
            async def provider(request):
                await asyncio.sleep(0.01)
                return ToolMessage(name="market_data", tool_call_id=request.tool_call["id"], content='{"price": 10}')
            handler = AsyncMock(side_effect=provider)
            middleware = ResearchTools()
            a,b = await asyncio.gather(middleware.awrap_tool_call(first, handler), middleware.awrap_tool_call(second, handler))
            self.assertEqual(handler.await_count, 1)
            self.assertEqual(unwrap(a)[0], unwrap(b)[0])
            self.assertEqual(b.tool_call_id, "b")
            self.assertEqual(context.run_budget.cache_hits, 1)
            self.assertFalse(MemoryContext().run_budget.cache)
        asyncio.run(run())

    def test_truncated_report_gets_one_continuation(self):
        from unittest.mock import Mock
        from langchain.agents.middleware.types import ModelResponse
        from langchain_core.messages import AIMessage
        context = MemoryContext()
        request = ModelRequest(model=ToolModel(responses=[]), messages=[HumanMessage(content="Research")],
                runtime=SimpleNamespace(context=context), state={"todos": [{"content": "Research"}]})
        handler = Mock(side_effect=[ModelResponse(result=[AIMessage(content="First", response_metadata={"finish_reason": "length"}, usage_metadata={"input_tokens":10,"output_tokens":10,"total_tokens":20})]),
            ModelResponse(result=[AIMessage(content="Last", usage_metadata={"input_tokens":10,"output_tokens":10,"total_tokens":20})])])
        result = ExecutionLimits().wrap_model_call(request, handler)
        self.assertEqual(handler.call_count, 2)
        self.assertEqual(result.result[-1].text, "First\nLast")
        self.assertEqual(context.run_budget.reserved, 0)
        self.assertEqual(context.run_budget.tokens, 40)

    def test_specialist_plans_do_not_consume_parent_replans(self):
        from orchestrator.policy import CapabilityPolicy
        budget = RunBudget()
        for i in range(8):
            budget.charge_tool("write_todos", {"todos": [{"content": str(i)}]}, track_plan=False)
        self.assertEqual(budget.replans, 0)
        self.assertEqual(budget.phase, "research")
        for role in ("research", "valuation", "risk", "synthesis"):
            self.assertTrue(CapabilityPolicy(role).allowed("write_todos", {}))

    def test_repeated_tools_and_plan_updates_end_research_without_crash(self):
        budget = RunBudget()
        self.assertTrue(budget.admit_tool("financial_news", {"symbol": "AAPL"}))
        self.assertTrue(budget.admit_tool("financial_news", {"symbol": "AAPL"}))
        self.assertFalse(budget.admit_tool("financial_news", {"symbol": "AAPL"}))
        self.assertEqual(budget.phase, "synthesis")
        self.assertFalse(budget.admit_tool("task", {"description": "Try again"}))
        budget = RunBudget()
        for i in range(12):
            self.assertTrue(budget.admit_tool("write_todos", {"todos": [{"content": str(i)}]}))
        self.assertFalse(budget.admit_tool("write_todos", {"todos": []}))
        self.assertEqual(budget.phase, "synthesis")
