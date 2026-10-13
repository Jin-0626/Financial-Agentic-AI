import asyncio
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from deepagents import create_deep_agent
from deepagents.backends import StateBackend

from app import routes
from app.context_type import MemoryContext
from orchestrator.policy import CapabilityPolicy
from orchestrator.budgets import RunBudget, ExecutionLimits
from agent_fixtures import ToolModel
import agent_fixtures as gathering


class Phase3PolicyTests(unittest.TestCase):
    def test_dispatch_denies_unadvertised_and_argument_scoped_tools(self):
        handler = Mock()
        for role, name, args in (("research", "execute", {"command": "python evil.py"}), ("synthesis", "market_data", {"symbol": "AAPL"}),
                                 ("risk", "market_data", {"symbol": "AAPL", "data_type": "financials"}), ("research", "write_file", {"file_path": "/tmp/x"})):
            request = SimpleNamespace(runtime=SimpleNamespace(context=None), tool_call={"name": name, "args": args, "id": "denied"})
            self.assertEqual(CapabilityPolicy(role).wrap_tool_call(request, handler).status, "error")
        handler.assert_not_called()

    def test_workspace_discovery_accepts_root_names_without_exposing_memory(self):
        policy = CapabilityPolicy("research")
        for name in ("ls", "glob", "grep"):
            self.assertTrue(policy.allowed(name, {"path": "/scripts"}))
            self.assertTrue(policy.allowed(name, {"path": "/skills"}))
            self.assertFalse(policy.allowed(name, {"path": "/scripts-private"}))
            self.assertFalse(policy.allowed(name, {"path": "/scripts/../memories"}))
        request = SimpleNamespace(tool_call={"name": "ls", "args": {"path": "/"}, "id": "root"})
        handler = Mock()
        result = policy.wrap_tool_call(request, handler)
        self.assertEqual(result.status, "success")
        self.assertEqual([row["path"] for row in json.loads(result.content)], ["/scripts/", "/skills/"])
        handler.assert_not_called()
        async_result = asyncio.run(policy.awrap_tool_call(request, handler))
        self.assertEqual(async_result.content, result.content)

    def test_default_discovery_is_scoped_to_scripts(self):
        from dataclasses import dataclass, replace
        @dataclass
        class Request:
            tool_call: dict
            runtime: object = None
            def override(self, **kwargs):
                return replace(self, **kwargs)
        policy = CapabilityPolicy("research")
        for name in ("grep", "glob"):
            for path in (None, "/", ".", ""):
                args = {"pattern": "YTL"}
                if path is not None:
                    args["path"] = path
                request = Request({"name": name, "args": args, "id": "search"})
                handler = Mock(return_value="found")
                self.assertEqual(policy.wrap_tool_call(request, handler), "found")
                self.assertEqual(handler.call_args.args[0].tool_call["args"]["path"], "/scripts/")
                self.assertEqual(request.tool_call["args"], args)

    def test_bounded_skill_reads_and_parent_memory_only(self):
        policy = CapabilityPolicy("research")
        self.assertTrue(policy.allowed("read_file", {"file_path": "/skills/valuation/SKILL.md", "offset": 0, "limit": 100}))
        for args in ({"file_path": "/skills/../.env", "limit": 100}, {"file_path": "C:/private.txt", "limit": 100}, {"file_path": "/skills/a", "limit": 201}, {"file_path": "/skills/a"}):
            self.assertFalse(policy.allowed("read_file", args))
        self.assertTrue(CapabilityPolicy("orchestrator").allowed("write_file", {"file_path": "/memories/habits.md"}))
        self.assertFalse(CapabilityPolicy("orchestrator").allowed("write_file", {"file_path": "/memories/AGENTS.md"}))

    def test_real_graph_cannot_dispatch_denied_data_type(self):
        model = ToolModel(responses=[AIMessage(content="", tool_calls=[{"name": "market_data", "args": {"symbol": "AAPL", "data_type": "financials"}, "id": "bad"}]), AIMessage(content="Requested capability unavailable")])
        provider = Mock()
        from app import financial_tools
        with patch.object(financial_tools, "stock_data", provider):
            graph = create_deep_agent(model=model, tools=[financial_tools._make_market_data_tool()], backend=StateBackend(), middleware=[CapabilityPolicy("risk")])
            result = graph.invoke({"messages": [HumanMessage(content="Get statements")]})
        provider.get_financials.assert_not_called()
        self.assertTrue(any(getattr(message, "status", None) == "error" for message in result["messages"]))

    def test_retired_routes_never_invoke_backend(self):
        api = FastAPI()
        api.include_router(routes.router, prefix="/api")
        with patch.object(routes, "_agent", None), TestClient(api) as client:
            for method, path in (("post", "/sandbox/execute"), ("post", "/execute-code"), ("get", "/sandbox/status"), ("get", "/sandbox/download")):
                self.assertEqual(getattr(client, method)("/api" + path).status_code, 410)

    def test_tool_budget_is_atomic_and_plan_progress_is_not_replanning(self):
        budget = RunBudget()
        def charge(_):
            try: budget.charge_tool("market_data", {}); return True
            except RuntimeError: return False
        with ThreadPoolExecutor(max_workers=8) as pool: outcomes = list(pool.map(charge, range(40)))
        self.assertEqual(sum(outcomes), 32)
        budget = RunBudget()
        for state in ("pending", "in_progress", "completed"):
            budget.charge_tool("write_todos", {"todos": [{"content": "Evidence", "status": state}]})
        self.assertEqual(budget.replans, 0)
        for i in range(3): budget.charge_tool("write_todos", {"todos": [{"content": str(i)}]})
        budget.charge_tool("write_todos", {"todos": [{"content": "fourth"}]})
        self.assertEqual(budget.phase, "synthesis")

    def test_token_usage_limits_and_production_unknown_usage(self):
        budget = RunBudget(max_tokens=50000)
        budget.charge_model(SimpleNamespace(result=[AIMessage(content="", usage_metadata={"input_tokens": 40000, "output_tokens": 10000, "total_tokens": 50000})]))
        with self.assertRaisesRegex(RuntimeError, "token budget"):
            budget.charge_model(SimpleNamespace(result=[AIMessage(content="", usage_metadata={"input_tokens": 1, "output_tokens": 0, "total_tokens": 1})]))
        with patch.dict("os.environ", {"APP_ENV": "production"}), self.assertRaisesRegex(RuntimeError, "unavailable"):
            RunBudget().charge_model(SimpleNamespace(result=[AIMessage(content="")]))

    def test_context_limits_are_applied_by_real_graph(self):
        context = MemoryContext(response_schema=None)
        model = ToolModel(responses=[AIMessage(content="", tool_calls=[{"name": "market_data", "args": {"symbol": "0157.KL"}, "id": "quote"}]), AIMessage(content="Retrieved")])
        graph = create_deep_agent(model=model, tools=[gathering.market_data], backend=StateBackend(), middleware=[ExecutionLimits()], context_schema=MemoryContext)
        graph.invoke({"messages": [HumanMessage(content="Quote")]}, context=context)
        self.assertEqual(context.run_budget.steps, 1)
        self.assertEqual(MemoryContext().run_budget.steps, 0)

    def test_async_task_concurrency_never_exceeds_four(self):
        async def exercise():
            budget = RunBudget()
            middleware = ExecutionLimits()
            active = peak = 0
            async def handler(request):
                nonlocal active, peak
                active += 1; peak = max(peak, active)
                await asyncio.sleep(0.02)
                active -= 1
            request = SimpleNamespace(runtime=SimpleNamespace(context=SimpleNamespace(run_budget=budget)), tool_call={"name": "task", "args": {}, "id": "task"})
            await asyncio.gather(*(middleware.awrap_tool_call(SimpleNamespace(runtime=request.runtime, tool_call={"name": "task", "args": {"description": f"Distinct assignment {i}"}, "id": str(i)}), handler) for i in range(8)))
            self.assertEqual(peak, 4)
        asyncio.run(exercise())

    def test_agent_compatibility_import_identity(self):
        import app.agent as legacy
        import orchestrator.agent as runtime
        self.assertIs(legacy.create_agent, runtime.create_agent)

    def test_budget_is_shared_with_specialist_context(self):
        context = MemoryContext(response_schema=None)
        specialist = ToolModel(responses=[AIMessage(content="", tool_calls=[{"name": "market_data", "args": {"symbol": "0157.KL"}, "id": "quote"}]), AIMessage(content="Retrieved")])
        parent = ToolModel(responses=[AIMessage(content="", tool_calls=[{"name": "task", "args": {"description": "Retrieve quote", "subagent_type": "research"}, "id": "delegate"}]), AIMessage(content="Done")])
        graph = create_deep_agent(model=parent, tools=[gathering.market_data], backend=StateBackend(), context_schema=MemoryContext,
                                 middleware=[ExecutionLimits()], subagents=[{"name": "research", "description": "Research", "system_prompt": "Gather evidence", "model": specialist,
                                 "tools": [gathering.market_data], "middleware": [ExecutionLimits()]}])
        graph.invoke({"messages": [HumanMessage(content="Research")]}, context=context)
        self.assertEqual(context.run_budget.steps, 2)


if __name__ == "__main__": unittest.main()
