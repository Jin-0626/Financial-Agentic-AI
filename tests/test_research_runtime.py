import asyncio
import json
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import financial_tools as finance
from app import sandbox as sb
from app import routes
from app.subagents import get_subagents_for_type
from app.research_integrity import RESEARCH_INTEGRITY


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        # Fake sandbox clients must not depend on a developer's private .env.
        settings = patch.dict(os.environ, {
            "OPEN_SANDBOX_API_KEY": "test-only-sandbox-key",
            "TAVILY_API_KEY": "",
            "OPEN_SANDBOX_CONFIG_FILE": "",
            "OPEN_SANDBOX_DOMAIN": "localhost:8080",
            "OPEN_SANDBOX_USE_SERVER_PROXY": "false",
        })
        settings.start()
        self.addCleanup(settings.stop)

    def manager(self):
        manager = sb.SandboxManager("test-image")
        manager._available = True
        return manager

    def backend(self):
        return Mock(execute=Mock(return_value=SimpleNamespace(exit_code=0, output="1")))

    def test_connection_failure_is_sanitized(self):
        with patch.dict(os.environ, {"OPEN_SANDBOX_API_KEY": "secret-value"}), patch.object(sb.SandboxSync, "create", side_effect=RuntimeError("401 secret-value")):
            manager = sb.SandboxManager("test-image")
            manager.start()
            self.assertFalse(manager.available)
            self.assertEqual(manager.error, "401 [redacted]")

    def test_probe_and_org_share_connection(self):
        container = Mock()
        backend = self.backend()
        with patch.object(sb.SandboxSync, "create", return_value=container) as create, patch.object(sb, "OpensandboxBackend", return_value=backend), patch.object(sb, "_provision_sandbox", return_value=(True, "ok")):
            manager = sb.SandboxManager("test-image")
            manager.start()
            try:
                self.assertIs(manager.get_backend("one"), backend)
                self.assertTrue(all(call.kwargs["connection_config"] is manager.connection_config for call in create.call_args_list))
            finally:
                manager.stop()

    def test_failed_provisioning_is_removed_and_retry_succeeds(self):
        backend = self.backend()
        containers = [Mock(), Mock()]
        with patch.object(sb.SandboxSync, "create", side_effect=containers), patch.object(sb, "OpensandboxBackend", return_value=backend), patch.object(sb, "_provision_sandbox", side_effect=[(False, "pip failed"), (True, "ok")]):
            manager = self.manager()
            self.assertIsNone(manager.get_backend("one"))
            containers[0].kill.assert_called_once()
            self.assertEqual(manager.get_sandbox_count(), 0)
            self.assertEqual(manager.execution_status("one")["error"], "pip failed")
            self.assertIs(manager.get_backend("one"), backend)
            self.assertIsNone(manager.execution_status("one")["error"])
            manager.stop()

    def test_timeout_removes_existing_entry(self):
        manager = self.manager()
        entry = sb._SandboxEntry(Mock(), self.backend())
        entry.wait_until_ready = Mock(return_value=False)
        manager._entries["one"] = entry
        self.assertIsNone(manager.get_backend("one"))
        self.assertTrue(entry.is_closed)
        self.assertIn("timed out", manager.execution_status("one")["error"])

    def test_concurrent_org_does_not_wait_for_global_lock(self):
        entered, release = threading.Event(), threading.Event()
        manager = self.manager()
        slow = sb._SandboxEntry(Mock(), self.backend())
        def wait(timeout=300):
            entered.set()
            return release.wait(2)
        slow.wait_until_ready = wait
        slow.provisioned = True
        fast = sb._SandboxEntry(Mock(), self.backend())
        fast.provisioned = True
        fast.ready_event.set()
        manager._entries.update(slow=slow, fast=fast)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(manager.get_backend, "slow")
            self.assertTrue(entered.wait(1))
            try:
                result = pool.submit(manager.get_backend, "fast").result(timeout=1)
                self.assertIs(result, fast.backend)
            finally:
                release.set()
                pending.result(timeout=2)
        manager.stop()

    def test_missing_directory_and_incomplete_upload_fail(self):
        with patch.object(sb, "LOCAL_SCRIPTS_DIR", Path("missing-provisioning-dir")):
            with self.assertRaises(FileNotFoundError):
                sb._provision_sandbox(self.backend())
        backend = self.backend()
        backend.upload_files.return_value = []
        with patch.object(sb, "_collect_dir_files", side_effect=[[], [("/scripts/test.py", b"x")]]):
            ok, error = sb._provision_sandbox(backend)
            self.assertFalse(ok)
            self.assertIn("upload", error)
            backend.execute.assert_not_called()

    def test_dependency_install_failure(self):
        backend = self.backend()
        backend.upload_files.return_value = [SimpleNamespace(error=None)]
        backend.execute.return_value = SimpleNamespace(exit_code=1, output="pip failed")
        with patch.object(sb, "_collect_dir_files", side_effect=[[], [("/scripts/test.py", b"x")]]):
            self.assertFalse(sb._provision_sandbox(backend)[0])

    def test_market_tool_independent_of_sandbox(self):
        provider = Mock()
        provider.get_quote.return_value = {"symbol": "0157.KL", "price": 0.5}
        with patch.object(finance, "stock_data", provider), patch.object(sb, "_sandbox_manager", None):
            result = json.loads(finance._make_market_data_tool().invoke({"symbol": "0157.KL"}))
            self.assertEqual(result["price"], 0.5)

    def test_news_empty_and_failed_are_distinct(self):
        import yfinance
        with patch.object(finance, "_fmp_client", None), patch.object(yfinance, "Ticker", return_value=SimpleNamespace(news=[])):
            result = json.loads(finance._make_news_tool().invoke({"symbol": "0157.KL"}))
            self.assertEqual(result["status"], "empty")
            self.assertNotIn("error", result)
        with patch.object(finance, "_fmp_client", None), patch.object(yfinance, "Ticker", side_effect=RuntimeError("HTTP 429")):
            result = json.loads(finance._make_news_tool().invoke({"symbol": "0157.KL"}))
            self.assertEqual(result["status"], "error")
            self.assertIn("429", result["attempts"][0]["error"])

    def test_news_fallback_is_independent_of_analytics_import(self):
        import yfinance
        fmp = Mock(api_key="configured")
        fmp._request.side_effect = RuntimeError("401")
        with patch.object(finance, "stock_data", None), patch.object(finance, "_fmp_client", fmp), patch.object(yfinance, "Ticker", return_value=SimpleNamespace(news=[{"content": {"title": "Verified headline"}}])):
            result = json.loads(finance._make_news_tool().invoke({"symbol": "0157.KL"}))
            self.assertEqual(result[0]["title"], "Verified headline")

    def test_status_and_agent_context_expose_failure(self):
        manager = self.manager()
        manager._available = False
        manager.error = "Authentication failed: 401"
        with patch.object(routes, "get_sandbox_manager", return_value=manager):
            status = asyncio.run(routes.sandbox_status())
            self.assertEqual(status, {"available": False, "active_count": 0, "error": manager.error})
            self.assertIn("401", routes._build_env_footer("one"))
            self.assertIn("independently", routes._build_env_footer("one"))

    def test_specialists_have_skills_and_shared_evidence_rules(self):
        agents = get_subagents_for_type("general")
        self.assertEqual(len(agents), 8)
        for agent in agents:
            self.assertEqual(agent["skills"], ["/skills/"])
            self.assertNotIn("tools", agent)  # inherit parent tools
            self.assertIn(RESEARCH_INTEGRITY, agent["system_prompt"])

    def test_specialist_inherits_market_tool_in_real_graph(self):
        from deepagents import create_deep_agent
        from deepagents.backends import StateBackend
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
        from langchain_core.messages import AIMessage
        class ToolModel(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "task", "args": {"description": "Retrieve quote and report errors", "subagent_type": "research"}, "id": "delegate"}]),
            AIMessage(content="", tool_calls=[{"name": "market_data", "args": {"symbol": "0157.KL"}, "id": "quote"}]),
            AIMessage(content="Provider returned HTTP 429; current valuation is unavailable."),
            AIMessage(content="market_data failed with HTTP 429; no company narrative is verified."),
        ])
        provider = Mock()
        provider.get_quote.return_value = {"error": "HTTP 429"}
        with patch.object(finance, "stock_data", provider):
            graph = create_deep_agent(
                model=model, system_prompt=RESEARCH_INTEGRITY,
                tools=finance.build_financial_tools(),
                subagents=[get_subagents_for_type("general")[0]], backend=StateBackend(),
            )
            result = graph.invoke({"messages": [{"role": "user", "content": "Research Focus Point"}]})
        provider.get_quote.assert_called_once_with("0157.KL")
        self.assertIn("HTTP 429", result["messages"][-1].content)

    def test_specialist_execution_uses_ready_org_backend(self):
        backend = self.backend()
        manager = self.manager()
        manager.get_backend = Mock(return_value=backend)
        proxy = sb._OrgScopedSandboxBackendProxy(manager)
        token = sb._set_current_org("one")
        try:
            proxy.execute("python3 /scripts/yf_data.py --help", timeout=15)
        finally:
            sb._reset_current_org(token)
        backend.execute.assert_called_once_with("python3 /scripts/yf_data.py --help", timeout=15)
        manager.get_backend.assert_called_once_with("one")


if __name__ == "__main__":
    unittest.main()
