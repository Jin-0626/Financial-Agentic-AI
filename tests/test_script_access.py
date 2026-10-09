import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.store.memory import InMemoryStore
from orchestrator import agent
from app.context_type import MemoryContext
from app.script_access import ReadOnlyScriptsBackend, SCRIPTS_ROOT, run_script, _capture
from app.research_tools import current_evidence
from orchestrator.policy import CapabilityPolicy


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class ScriptAccessTests(unittest.TestCase):
    def test_real_factory_lists_reads_and_runs_script(self):
        model = ToolModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "ls",
                            "args": {"path": "/scripts/data_sources/"},
                            "id": "list",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {
                                "file_path": "/scripts/AGENTS.md",
                                "offset": 0,
                                "limit": 100,
                            },
                            "id": "read",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "run_script",
                            "args": {
                                "script": "bnm_data.py",
                                "arguments": ["available"],
                            },
                            "id": "run",
                        }
                    ],
                ),
                AIMessage(content="BNM supports USD and MYR exchange-rate research."),
            ]
        )
        with patch.object(agent, "MODEL_NAME", model):
            graph = agent.create_agent(None, InMemoryStore())
            state = graph.invoke(
                {
                    "messages": [
                        HumanMessage(content="Discover BNM script capabilities")
                    ]
                },
                context=MemoryContext(response_schema=None),
            )
        tools = [m for m in state["messages"] if isinstance(m, ToolMessage)]
        self.assertTrue(
            any(m.name == "ls" and "bnm_data.py" in m.content for m in tools)
        )
        self.assertTrue(
            any(
                m.name == "read_file" and "Financial research scripts" in m.content
                for m in tools
            )
        )
        self.assertFalse(any(m.status == "error" for m in tools))
        receipts = current_evidence(state["messages"])
        self.assertEqual(len(receipts), 1)
        self.assertIn("USD", json.loads(receipts[0].content)["data"]["all_currencies"])

    def test_research_specialist_inherits_scripts_route_and_runner(self):
        model = ToolModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {
                                "description": "Read the BNM documentation and list supported currencies",
                                "subagent_type": "research",
                            },
                            "id": "delegate",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {
                                "file_path": "/scripts/AGENTS.md",
                                "offset": 0,
                                "limit": 100,
                            },
                            "id": "read",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "run_script",
                            "args": {
                                "script": "bnm_data.py",
                                "arguments": ["available"],
                            },
                            "id": "run",
                        }
                    ],
                ),
                AIMessage(content="BNM supports USD."),
                AIMessage(content="The specialist verified BNM supports USD."),
            ]
        )
        with patch.object(agent, "MODEL_NAME", model):
            graph = agent.create_agent(None, InMemoryStore())
            state = graph.invoke(
                {"messages": [HumanMessage(content="Delegate BNM discovery")]},
                context=MemoryContext(),
            )
        receipts = current_evidence(state["messages"])
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0].name, "run_script")
        self.assertIn("USD", json.loads(receipts[0].content)["data"]["all_currencies"])

    def test_scripts_are_read_only_sync_and_async(self):
        import asyncio

        backend = ReadOnlyScriptsBackend(root_dir=SCRIPTS_ROOT, virtual_mode=True)
        before = (SCRIPTS_ROOT / "AGENTS.md").read_bytes()
        self.assertIsNotNone(backend.write("/AGENTS.md", "bad").error)
        self.assertIsNotNone(backend.edit("/AGENTS.md", "Financial", "bad").error)
        self.assertIsNotNone(backend.delete("/AGENTS.md").error)
        self.assertIsNotNone(asyncio.run(backend.awrite("/AGENTS.md", "bad")).error)
        self.assertEqual(
            backend.upload_files([("/bad.py", b"bad")])[0].error, "permission_denied"
        )
        self.assertEqual((SCRIPTS_ROOT / "AGENTS.md").read_bytes(), before)

    def test_runner_denies_unknown_paths_commands_and_shell(self):
        for name, args in [
            ("../secret.py", ["available"]),
            ("bnm_data.py", ["available; echo bad"]),
            ("sec_data.py", ["filing_content", "http://localhost/"]),
        ]:
            with patch("app.script_access._capture") as execute:
                result = json.loads(
                    run_script.invoke({"script": name, "arguments": args})
                )
                self.assertEqual(result["status"], "error")
                execute.assert_not_called()

    def test_roles_only_discover_script_paths_and_research_can_run(self):
        policy = CapabilityPolicy("research")
        self.assertTrue(policy.allowed("ls", {"path": "/scripts/"}))
        self.assertTrue(
            policy.allowed(
                "read_file", {"file_path": "/scripts/AGENTS.md", "limit": 100}
            )
        )
        self.assertFalse(
            policy.allowed("read_file", {"file_path": "/scripts/../.env", "limit": 100})
        )
        self.assertFalse(policy.allowed("ls", {"path": "/"}))
        self.assertFalse(
            policy.allowed("write_file", {"file_path": "/scripts/AGENTS.md"})
        )
        self.assertFalse(CapabilityPolicy("synthesis").allowed("run_script", {}))

    def test_output_limit_kills_large_subprocess(self):
        with self.assertRaisesRegex(ValueError, "output exceeded"):
            _capture([sys.executable, "-B", "-c", "print('x'*100000)"])

    def test_sec_requires_explicit_contact_identity(self):
        with patch.dict("os.environ", {"SEC_USER_AGENT": ""}):
            result = json.loads(
                run_script.invoke(
                    {"script": "sec_data.py", "arguments": ["available_form_types"]}
                )
            )
        self.assertEqual(result["status"], "error")
        self.assertIn("SEC_USER_AGENT", result["error"])
