import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch
from langchain_core.messages import AIMessage
from orchestrator.budgets import RunBudget, ExecutionLimits
from app.context_type import MemoryContext


def response(input_tokens, output_tokens):
    return SimpleNamespace(result=[AIMessage(content="", usage_metadata={
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens})])


class TokenBudgetTests(unittest.TestCase):
    def test_zero_initial_usage_and_configured_boundary(self):
        with patch.dict("os.environ", {"RESEARCH_MAX_TOKENS": "50000"}):
            budget = RunBudget()
        self.assertEqual(budget.tokens, 0)
        budget.charge_model(response(40000, 10000))
        self.assertEqual(budget.tokens, 50000)
        with self.assertRaisesRegex(RuntimeError, "50,000 used; 50,000 limit"):
            budget.check_model_budget()
        with self.assertRaisesRegex(RuntimeError, "50,001 used"):
            budget.charge_model(response(1, 0))

    def test_default_supports_multiple_research_calls_and_new_turn_resets(self):
        with patch.dict("os.environ", {}, clear=True):
            first, second = MemoryContext(), MemoryContext()
        for _ in range(3):
            first.run_budget.charge_model(response(20000, 1000))
        self.assertEqual(first.run_budget.tokens, 63000)
        self.assertEqual(first.run_budget.max_tokens, 1000000)
        self.assertEqual(second.run_budget.tokens, 0)

    def test_output_limits_and_near_exhaustion_guidance(self):
        from langchain.agents.middleware.types import ModelRequest
        from langchain_core.messages import SystemMessage
        from agent_fixtures import ToolModel
        request = ModelRequest(model=ToolModel(responses=[]), messages=[], state={},
                               system_message=SystemMessage(content="Existing instructions"))
        budget = RunBudget(max_tokens=100)
        self.assertEqual(ExecutionLimits().bounded_request(request, budget).model_settings["max_tokens"], 800)
        request = request.override(state={"todos": [{"content": "Research"}]})
        self.assertEqual(ExecutionLimits().bounded_request(request, budget).model_settings["max_tokens"], 3000)
        self.assertEqual(ExecutionLimits("specialist").bounded_request(request, budget).model_settings["max_tokens"], 1500)
        budget.tokens = 80
        bounded = ExecutionLimits().bounded_request(request, budget)
        self.assertIn("stop expanding research", bounded.system_message.text)
        self.assertIn("Existing instructions", bounded.system_message.text)
        self.assertEqual(budget.snapshot()["tokens_used"], 80)

    def test_ollama_limit_uses_options_without_invalid_client_keyword(self):
        import inspect
        from langchain_ollama import ChatOllama
        from langchain.agents.middleware.types import ModelRequest
        from langchain_core.messages import HumanMessage
        from ollama import AsyncClient
        model = ChatOllama(model="fixture", num_predict=1000, num_ctx=8192)
        request = ModelRequest(model=model, messages=[HumanMessage(content="Research")],
                               state={}, model_settings={"max_tokens": 700,
                               "options": {"temperature": 0.2}})
        bounded = ExecutionLimits().bounded_request(request, RunBudget())
        self.assertNotIn("max_tokens", bounded.model_settings)
        self.assertEqual(bounded.model_settings["options"]["num_predict"], 700)
        self.assertEqual(bounded.model_settings["options"]["temperature"], 0.2)
        params = model._chat_params(bounded.messages, **bounded.model_settings)
        inspect.signature(AsyncClient.chat).bind(None, **params)
        self.assertEqual(params["options"]["num_predict"], 700)
        self.assertEqual(params["options"]["num_ctx"], 8192)

    def test_wall_budget_blocks_tools_and_models(self):
        budget = RunBudget(max_wall_s=1)
        budget.started_at -= 2
        with self.assertRaisesRegex(RuntimeError, "wall-clock"):
            budget.check_model_budget()
        with self.assertRaisesRegex(RuntimeError, "wall-clock"):
            budget.charge_tool("market_data", {})

    def test_invalid_configuration_fails_clearly(self):
        for value in ("0", "-1", "invalid", "1.5", ""):
            with self.subTest(value=value), patch.dict("os.environ", {"RESEARCH_MAX_TOKENS": value}):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    RunBudget()

    def test_sync_and_async_do_not_call_provider_after_exhaustion(self):
        budget = RunBudget(max_tokens=10)
        request = SimpleNamespace(runtime=SimpleNamespace(context=SimpleNamespace(run_budget=budget)))
        middleware = ExecutionLimits()
        sync = Mock(return_value=response(5, 5))
        middleware.wrap_model_call(request, sync)
        with self.assertRaisesRegex(RuntimeError, "token budget exhausted"):
            middleware.wrap_model_call(request, sync)
        sync.assert_called_once()
        async_handler = AsyncMock(return_value=response(1, 1))
        with self.assertRaisesRegex(RuntimeError, "token budget exhausted"):
            asyncio.run(middleware.awrap_model_call(request, async_handler))
        async_handler.assert_not_awaited()

    def test_async_usage_is_charged_and_overrun_remains_visible(self):
        budget = RunBudget(max_tokens=10)
        request = SimpleNamespace(runtime=SimpleNamespace(context=SimpleNamespace(run_budget=budget)))
        handler = AsyncMock(return_value=response(8, 3))
        with self.assertRaisesRegex(RuntimeError, "11 used; 10 limit"):
            asyncio.run(ExecutionLimits().awrap_model_call(request, handler))
        self.assertEqual(budget.tokens, 11)
        handler.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
