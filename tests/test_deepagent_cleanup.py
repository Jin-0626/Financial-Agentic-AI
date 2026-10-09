"""Regression coverage for native Deep Agent cleanup and provider reuse."""

import asyncio
import json
import unittest
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.store.memory import InMemoryStore
from pydantic import BaseModel, ValidationError

from agent_fixtures import ToolModel
from app.context_type import MemoryContext
from app.evidence import unwrap
from app.prompts import SPECIALIST_RESEARCH_PROMPT
from app.research_integrity import RESEARCH_INTEGRITY
from app.research_tools import ResearchTools
from app.subagents import get_subagents_for_type
from orchestrator import agent
from orchestrator.policy import CapabilityPolicy
from orchestrator.tool_registry import ROLE_TOOLS


@dataclass
class Request:
    tool_call: dict
    runtime: object
    tools: tuple = ()
    tool: object = None

    def override(self, **kwargs):
        return replace(self, **kwargs)


def request(call_id, context=None, name="market_data", args=None):
    return Request(
        tool_call={"name": name, "id": call_id, "args": args if args is not None else {"symbol": "AAPL"}},
        runtime=SimpleNamespace(context=context, state={}),
    )


class ProviderReuseTests(unittest.IsolatedAsyncioTestCase):
    async def concurrent(self, provider):
        context = MemoryContext()
        started, finish = asyncio.Event(), asyncio.Event()

        async def execute(req):
            started.set()
            await finish.wait()
            return await provider(req)

        handler = AsyncMock(side_effect=execute)
        middleware = ResearchTools()
        owner = asyncio.create_task(middleware.awrap_tool_call(request("owner", context), handler))
        self.addAsyncCleanup(self.cancel, owner)
        await asyncio.wait_for(started.wait(), 1)
        waiters = [asyncio.create_task(middleware.awrap_tool_call(request(str(i), context), handler)) for i in range(2)]
        for waiter in waiters:
            self.addAsyncCleanup(self.cancel, waiter)
        await asyncio.sleep(0)
        finish.set()
        results = await asyncio.wait_for(asyncio.gather(owner, *waiters, return_exceptions=True), 1)
        return context, handler, results

    async def cancel(self, task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_error_outcome_is_shared_but_not_cached(self):
        async def provider(req):
            return ToolMessage(name="market_data", tool_call_id=req.tool_call["id"], content='{"status":"error","error":"fixture outage"}')

        context, handler, results = await self.concurrent(provider)
        self.assertEqual(handler.await_count, 1)
        self.assertFalse(context.run_budget.cache)
        self.assertFalse(context.run_budget.inflight)
        ids = set()
        for result, call_id in zip(results, ("owner", "0", "1")):
            payload, metadata = unwrap(result)
            self.assertEqual(result.status, "error")
            self.assertEqual(result.tool_call_id, call_id)
            self.assertEqual(payload["error"], "fixture outage")
            ids.add(metadata["id"])
        self.assertEqual(len(ids), 3)
        await ResearchTools().awrap_tool_call(request("later", context), handler)
        self.assertEqual(handler.await_count, 2)

    async def test_success_cache_keeps_fresh_receipts_and_raw_output(self):
        raw = ToolMessage(name="market_data", tool_call_id="owner", content='{"price":10}')

        async def provider(req):
            return raw

        context, handler, results = await self.concurrent(provider)
        self.assertEqual(handler.await_count, 1)
        later = await ResearchTools().awrap_tool_call(request("later", context), handler)
        self.assertEqual(handler.await_count, 1)
        self.assertEqual(raw.content, '{"price":10}')
        self.assertIsNone(raw.artifact)
        all_results = [*results, later]
        self.assertTrue(all(unwrap(result)[0] == {"price": 10} for result in all_results))
        self.assertEqual(len({unwrap(result)[1]["id"] for result in all_results}), 4)

    async def test_transient_retry_is_shared(self):
        calls = 0

        async def provider(req):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TimeoutError("fixture timeout")
            return ToolMessage(name="market_data", tool_call_id=req.tool_call["id"], content='{"price":10}')

        context, handler, results = await self.concurrent(provider)
        self.assertEqual(handler.await_count, 2)
        self.assertTrue(all(result.status == "success" for result in results))
        self.assertFalse(context.run_budget.inflight)

    async def test_exhausted_retry_is_shared_without_cache(self):
        async def provider(req):
            raise ConnectionError("fixture unavailable")

        context, handler, results = await self.concurrent(provider)
        self.assertEqual(handler.await_count, 2)
        self.assertTrue(all(isinstance(result, ConnectionError) for result in results))
        self.assertFalse(context.run_budget.inflight)
        self.assertFalse(context.run_budget.cache)

    async def test_unexpected_exception_is_shared_without_retry(self):
        async def provider(req):
            raise ValueError("fixture defect")

        context, handler, results = await self.concurrent(provider)
        self.assertEqual(handler.await_count, 1)
        self.assertTrue(all(isinstance(result, ValueError) for result in results))
        self.assertFalse(context.run_budget.inflight)

    async def test_validation_failure_uses_each_call_id(self):
        class Arguments(BaseModel):
            symbol: str

        try:
            Arguments.model_validate({"symbol": None})
        except ValidationError as error:
            validation = error

        async def provider(req):
            raise validation

        context, handler, results = await self.concurrent(provider)
        self.assertEqual(handler.await_count, 1)
        for result, call_id in zip(results, ("owner", "0", "1")):
            self.assertEqual(result.tool_call_id, call_id)
            self.assertEqual(result.status, "error")
            self.assertEqual(json.loads(result.content)["fields"], ["symbol"])
        self.assertFalse(context.run_budget.inflight)

    async def test_owner_cancellation_cancels_waiters(self):
        context = MemoryContext()
        started = asyncio.Event()

        async def provider(req):
            started.set()
            await asyncio.Event().wait()

        handler = AsyncMock(side_effect=provider)
        middleware = ResearchTools()
        owner = asyncio.create_task(middleware.awrap_tool_call(request("owner", context), handler))
        self.addAsyncCleanup(self.cancel, owner)
        await asyncio.wait_for(started.wait(), 1)
        waiter = asyncio.create_task(middleware.awrap_tool_call(request("waiter", context), handler))
        self.addAsyncCleanup(self.cancel, waiter)
        await asyncio.sleep(0)
        owner.cancel()
        results = await asyncio.wait_for(asyncio.gather(owner, waiter, return_exceptions=True), 1)
        self.assertTrue(all(isinstance(result, asyncio.CancelledError) for result in results))
        self.assertEqual(handler.await_count, 1)
        self.assertFalse(context.run_budget.inflight)

    async def test_waiter_cancellation_does_not_cancel_owner(self):
        context = MemoryContext()
        started, finish = asyncio.Event(), asyncio.Event()

        async def provider(req):
            started.set()
            await finish.wait()
            return ToolMessage(name="market_data", tool_call_id=req.tool_call["id"], content='{"price":10}')

        handler = AsyncMock(side_effect=provider)
        middleware = ResearchTools()
        owner = asyncio.create_task(middleware.awrap_tool_call(request("owner", context), handler))
        self.addAsyncCleanup(self.cancel, owner)
        await asyncio.wait_for(started.wait(), 1)
        waiter = asyncio.create_task(middleware.awrap_tool_call(request("waiter", context), handler))
        self.addAsyncCleanup(self.cancel, waiter)
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertFalse(next(iter(context.run_budget.inflight.values())).cancelled())
        finish.set()
        result = await asyncio.wait_for(owner, 1)
        self.assertEqual(result.status, "success")
        self.assertEqual(handler.await_count, 1)
        self.assertFalse(context.run_budget.inflight)

    async def test_request_budgets_and_private_tools_are_not_shared(self):
        handler = AsyncMock(side_effect=lambda req: ToolMessage(name=req.tool_call["name"], tool_call_id=req.tool_call["id"], content='{"value":10}'))
        middleware = ResearchTools()
        await asyncio.gather(*(middleware.awrap_tool_call(request(str(i), MemoryContext()), handler) for i in range(2)))
        self.assertEqual(handler.await_count, 2)
        context = MemoryContext()
        for name in ("portfolio_analytics", "run_script", "calculate_historical_var"):
            handler.reset_mock()
            await asyncio.gather(*(middleware.awrap_tool_call(request(str(i), context, name), handler) for i in range(2)))
            self.assertEqual(handler.await_count, 2)
        self.assertFalse(context.run_budget.cache)
        self.assertFalse(context.run_budget.inflight)


class CapabilityParityTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_sync_async_parity_for_all_roles(self):
        cases = [
            ("ls", {"path": "/"}), ("grep", {"pattern": "financial"}),
            ("glob", {"path": "/scripts/../memories", "pattern": "*"}),
            ("read_file", {"file_path": "/skills/x/SKILL.md", "limit": 200}),
            ("read_file", {"file_path": "/scripts/x", "limit": True}),
            ("read_file", {"file_path": "/scripts/x", "limit": 201}),
            ("write_file", {"file_path": "/memories/habits.md"}),
            ("market_data", {"data_type": "financials"}),
            ("market_data", {"data_type": "history"}),
            ("task", {}), ("execute", {}), ("write_todos", {}),
        ]
        for role in ROLE_TOOLS:
            for mode in (None, "financial"):
                for name, args in cases:
                    with self.subTest(role=role, mode=mode, tool=name, args=args):
                        context = SimpleNamespace(response_schema=mode, run_budget=None)
                        req = request("call", context, name, args)
                        sync, async_handler = Mock(return_value="allowed"), AsyncMock(return_value="allowed")
                        policy = CapabilityPolicy(role)
                        a = policy.wrap_tool_call(req, sync)
                        b = await policy.awrap_tool_call(req, async_handler)
                        self.assertEqual(a, b)
                        self.assertEqual(sync.call_count, async_handler.await_count)
                        if sync.called:
                            self.assertEqual(sync.call_args.args[0], async_handler.call_args.args[0])

    async def test_visible_tools_sync_async_parity_for_all_roles(self):
        names = set().union(*ROLE_TOOLS.values()) | {"read_file", "ls", "glob", "grep", "write_file", "edit_file", "execute"}
        tools = tuple(SimpleNamespace(name=name) for name in sorted(names))
        for role in ROLE_TOOLS:
            for mode in (None, "financial"):
                with self.subTest(role=role, mode=mode):
                    context = SimpleNamespace(response_schema=mode)
                    req = replace(request("call", context), tools=tools)
                    sync, async_handler = Mock(), AsyncMock()
                    policy = CapabilityPolicy(role)
                    policy.wrap_model_call(req, sync)
                    await policy.awrap_model_call(req, async_handler)
                    self.assertEqual(sync.call_args.args[0].tools, async_handler.call_args.args[0].tools)
                    visible = {tool.name for tool in sync.call_args.args[0].tools}
                    self.assertNotIn("execute", visible)
                    if role == "synthesis":
                        self.assertNotIn("market_data", visible)
                    if role == "orchestrator" and mode:
                        self.assertNotIn("market_data", visible)
                        self.assertIn("task", visible)


class SpecialistCompositionTests(unittest.TestCase):
    def test_shared_instructions_once_for_every_factory_specialist(self):
        with patch.object(agent, "create_deep_agent") as build:
            agent.create_agent(None, InMemoryStore())
        specs = build.call_args.kwargs["subagents"]
        self.assertEqual(len(specs), 9)
        for spec in specs:
            with self.subTest(specialist=spec["name"]):
                self.assertEqual(spec["system_prompt"].count(SPECIALIST_RESEARCH_PROMPT), 1)
                self.assertEqual(spec["system_prompt"].count(RESEARCH_INTEGRITY), 1)
                self.assertNotIn("Current UTC date:", spec["system_prompt"])
                self.assertEqual(spec["skills"], ["/skills/"])
        self.assertEqual(len(get_subagents_for_type("general")), 8)

    def test_compiled_agent_clock_changes_on_subsequent_invocations(self):
        seen = []

        class RecordingModel(ToolModel):
            def _generate(self, messages, **kwargs):
                seen.extend(message.text for message in messages if message.type == "system")
                return super()._generate(messages, **kwargs)

        model = RecordingModel(responses=[AIMessage(content="Hello"), AIMessage(content="Hello again")])
        with patch.object(agent, "MODEL_NAME", model):
            graph = agent.create_agent(None, InMemoryStore())
        for day in (9, 10):
            with patch("orchestrator.freshness.now_utc", return_value=datetime(2026, 10, day, 0, tzinfo=timezone.utc)):
                graph.invoke({"messages": [HumanMessage(content="Hello")]}, context=MemoryContext())
        self.assertIn("2026-10-09", seen[0])
        self.assertIn("2026-10-10", seen[1])
        self.assertNotIn("Current UTC date:", seen[1])
