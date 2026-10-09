import json
import unittest
from unittest.mock import patch, Mock
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.store.memory import InMemoryStore
from orchestrator import agent
from app.context_type import MemoryContext
from app.research_tools import current_evidence
from app.research_output import format_result

class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs): return self

class DeepAgentArchitectureTests(unittest.TestCase):
    def test_native_planning_and_readable_specialist_summary_with_receipts(self):
        todos = [{"content": "Gather evidence", "status": "pending"}]
        report = "## Executive Summary\n\nBNM lists USD as a supported currency.\n\n## Sources\n\nBank Negara Malaysia currency catalog."
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "write_todos", "args": {"todos": todos}, "id": "plan"}]),
            AIMessage(content="", tool_calls=[{"name": "task", "args": {"description": "Check BNM currencies", "subagent_type": "research"}, "id": "delegate"}]),
            AIMessage(content="", tool_calls=[{"name": "run_script", "args": {"script": "bnm_data.py", "arguments": ["available"]}, "id": "data"}]),
            AIMessage(content="BNM lists USD; source: Bank Negara Malaysia currency catalog."),
            AIMessage(content=report)])
        with patch.object(agent, "MODEL_NAME", model):
            graph = agent.create_agent(None, InMemoryStore())
            state = graph.invoke({"messages": [HumanMessage(content="Research BNM currencies")]}, context=MemoryContext())
        task = next(m for m in state["messages"] if isinstance(m, ToolMessage) and m.name == "task")
        self.assertTrue(task.content.startswith("BNM lists USD"))
        self.assertNotIn("research_evidence_v1", task.content)
        self.assertIn("specialist_evidence", task.artifact)
        self.assertEqual(current_evidence(state["messages"])[0].name, "run_script")
        self.assertEqual(format_result(state, "t")["result"], report)
        self.assertEqual(state["todos"], todos)

    def test_factory_uses_native_planning_and_shared_scripts(self):
        with patch.object(agent, "MODEL_NAME", ToolModel(responses=[AIMessage(content="Answer")])), patch.object(agent, "create_deep_agent") as build:
            agent.create_agent(None, InMemoryStore())
        config = build.call_args.kwargs
        self.assertNotIn("response_format", config)
        self.assertEqual([type(m).__name__ for m in config["middleware"]].count("TodoListMiddleware"), 1)
        self.assertIn("/scripts/AGENTS.md", config["memory"])
        for specialist in config["subagents"]:
            self.assertIn("/scripts/", specialist["system_prompt"])
            self.assertEqual(specialist["skills"], ["/skills/"])

    def test_research_parent_cannot_bypass_specialist_retrieval(self):
        from types import SimpleNamespace
        from orchestrator.policy import CapabilityPolicy
        policy = CapabilityPolicy("orchestrator")
        runtime = SimpleNamespace(context=MemoryContext())
        request = SimpleNamespace(runtime=runtime, tool_call={"name": "run_script", "id": "direct", "args": {"script": "bnm_data.py", "arguments": ["available"]}})
        handler = Mock()
        denied = policy.wrap_tool_call(request, handler)
        self.assertEqual(denied.status, "error")
        handler.assert_not_called()
        self.assertFalse(CapabilityPolicy("research").delegated_tools(runtime))
        self.assertFalse(policy.delegated_tools(SimpleNamespace(context=MemoryContext(response_schema=None))))

    def test_parent_discovers_task_instead_of_low_level_retrieval(self):
        from types import SimpleNamespace
        from orchestrator.policy import CapabilityPolicy
        request = Mock(runtime=SimpleNamespace(context=MemoryContext()), tools=[SimpleNamespace(name=name) for name in ["task", "company_search", "market_data", "run_script", "write_todos"]])
        handler = Mock()
        CapabilityPolicy("orchestrator").wrap_model_call(request, handler)
        visible = [tool.name for tool in request.override.call_args.kwargs["tools"]]
        self.assertEqual(visible, ["task", "company_search", "write_todos"])
