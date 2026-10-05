import asyncio
import importlib
import json
import math
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from app import routes, sandbox as sb, financial_tools as finance

ROOT = Path(__file__).resolve().parents[1]


class VerifiedDefectTests(unittest.TestCase):
    def setUp(self):
        # Fake sandbox clients must not depend on a developer's private .env.
        settings = patch.dict(os.environ, {
            "OPEN_SANDBOX_API_KEY": "test-only-sandbox-key",
            "OPEN_SANDBOX_CONFIG_FILE": "",
            "OPEN_SANDBOX_DOMAIN": "localhost:8080",
            "OPEN_SANDBOX_USE_SERVER_PROXY": "false",
        })
        settings.start()
        self.addCleanup(settings.stop)

    def large_file(self):
        return SimpleNamespace(is_small=False, full_content=None, filename="prices.csv", file_type="csv", row_count=10000, columns=["price"], preview="price\n1")

    def test_large_upload_uses_owner_backend_and_reuploads(self):
        backend = Mock()
        backend.upload_files.return_value = [SimpleNamespace(error=None)]
        token = sb._set_current_org(None)
        try:
            with patch.dict(routes._uploaded_files, {"large": self.large_file()}), patch.dict(routes._file_org_map, {"large": "org-a"}), patch.dict(routes._file_content_cache, {"large": b"price\n1"}), patch.object(routes, "_get_org_backend", return_value=backend) as resolve, patch.object(routes, "get_sandbox_manager", return_value=None):
                message, org = routes._prepare_message_context("analyze", "large", "org-a")
                resolve.assert_called_once_with("org-a")
                self.assertEqual(sb._get_current_org(), "org-a")
                self.assertEqual(org, "org-a")
                self.assertIn("/workspace/large_prices.csv", message)
                backend.upload_files.assert_called_once_with([("/workspace/large_prices.csv", b"price\n1")])
        finally:
            sb._reset_current_org(token)

    def test_large_upload_without_sandbox_uses_preview(self):
        token = sb._set_current_org(None)
        try:
            with patch.dict(routes._uploaded_files, {"large": self.large_file()}), patch.dict(routes._file_org_map, {"large": "org-a"}), patch.object(routes, "_get_org_backend", return_value=None), patch.object(routes, "get_sandbox_manager", return_value=None):
                message, _ = routes._prepare_message_context("analyze", "large", "org-a")
                self.assertIn("only preview", message)
        finally:
            sb._reset_current_org(token)

    def test_file_ownership_403_before_backend_access(self):
        with patch.dict(routes._uploaded_files, {"large": self.large_file()}), patch.dict(routes._file_org_map, {"large": "org-a"}), patch.object(routes, "_get_org_backend") as resolve:
            with self.assertRaises(HTTPException) as error:
                routes._prepare_message_context("analyze", "large", "org-b")
            self.assertEqual(error.exception.status_code, 403)
            resolve.assert_not_called()

    def test_http_file_ownership_403_in_both_chat_routes(self):
        from fastapi import FastAPI
        api = FastAPI()
        api.include_router(routes.router, prefix="/api")
        with patch.dict(routes._uploaded_files, {"large": self.large_file()}), patch.dict(routes._file_org_map, {"large": "org-a"}), patch.object(routes, "_agent", Mock()), patch.object(routes, "_ensure_user_habits"), patch.object(routes, "_get_org_backend") as resolve:
            with TestClient(api) as client:
                for path in ("/api/chat-with-file", "/api/chat-with-file/stream"):
                    with self.subTest(path=path):
                        response = client.post(path, json={"message": "analyze", "file_id": "large", "org_id": "org-b"})
                        self.assertEqual(response.status_code, 403)
            resolve.assert_not_called()

    def test_recent_activity_between_scan_and_lock_is_not_reclaimed(self):
        manager = sb.SandboxManager("fake")
        entry = sb._SandboxEntry(Mock(), Mock())
        entry.last_used_at = 0
        class HookLock:
            def __enter__(self):
                entry.touch()
            def __exit__(self, *args):
                return False
        manager._entries["org"] = entry
        manager._provision_locks["org"] = HookLock()
        with patch.object(sb.time, "time", return_value=1000):
            manager._reap_expired()
        self.assertFalse(entry.is_closed)
        self.assertIs(manager._entries["org"], entry)
        entry.sandbox.kill.assert_not_called()

    def test_replaced_entry_is_not_reclaimed(self):
        manager = sb.SandboxManager("fake")
        old, replacement = sb._SandboxEntry(Mock(), Mock()), sb._SandboxEntry(Mock(), Mock())
        old.last_used_at = 0
        class HookLock:
            def __enter__(self):
                manager._entries["org"] = replacement
            def __exit__(self, *args):
                return False
        manager._entries["org"] = old
        manager._provision_locks["org"] = HookLock()
        with patch.object(sb.time, "time", return_value=1000):
            manager._reap_expired()
        self.assertFalse(replacement.is_closed)
        self.assertIs(manager._entries["org"], replacement)

    def test_expired_entry_is_reclaimed(self):
        import threading
        manager = sb.SandboxManager("fake")
        entry = sb._SandboxEntry(Mock(), Mock())
        entry.last_used_at = 0
        manager._entries["org"] = entry
        manager._provision_locks["org"] = threading.Lock()
        with patch.object(sb.time, "time", return_value=1000):
            manager._reap_expired()
        self.assertTrue(entry.is_closed)
        self.assertNotIn("org", manager._entries)

    def treasury(self, quotes=None, exception=None, fmp=None):
        provider = Mock()
        if exception:
            provider.get_batch_quotes.side_effect = exception
        else:
            provider.get_batch_quotes.return_value = quotes
        with patch.object(finance, "stock_data", provider), patch.object(finance, "_fmp_client", fmp):
            return json.loads(finance._make_economics_tool().invoke({"indicator": "treasury"}))

    def quotes(self):
        return [{"symbol": symbol, "price": 4.0} for symbol in ("^IRX", "^FVX", "^TNX", "^TYX")]

    def test_treasury_valid_results(self):
        result = self.treasury(self.quotes())
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["treasury_yield_proxies"], self.quotes())
        self.assertEqual(result["provider_errors"], [])
        self.assertIsNone(result["error"])

    def test_treasury_empty_response(self):
        result = self.treasury([])
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["treasury_yield_proxies"], [])
        self.assertTrue(result["provider_errors"])

    def test_treasury_provider_exception_is_sanitized(self):
        with patch.dict(os.environ, {"FMP_API_KEY": "fixture-private-key"}):
            result = self.treasury(exception=RuntimeError("HTTP 401 fixture-private-key"))
        self.assertEqual(result["status"], "error")
        self.assertNotIn("fixture-private-key", json.dumps(result))
        self.assertIn("401", json.dumps(result))

    def test_treasury_error_containing_response(self):
        result = self.treasury([{"error": "HTTP 429"}])
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["treasury_yield_proxies"], [])
        self.assertIn("429", json.dumps(result["provider_errors"]))

    def test_treasury_partial_success_preserves_valid_quotes(self):
        valid = self.quotes()[:2]
        result = self.treasury(valid + [{"symbol": "^TNX", "error": "HTTP 429"}, {"symbol": "^TYX", "price": None}])
        self.assertEqual(result["status"], "partial_success")
        self.assertEqual(result["treasury_yield_proxies"], valid)
        self.assertTrue(result["provider_errors"])

    def test_treasury_fmp_failure_kept_when_yahoo_succeeds(self):
        fmp = Mock(api_key="fixture")
        fmp.get_treasury_rates.side_effect = RuntimeError("HTTP 403")
        result = self.treasury(self.quotes(), fmp=fmp)
        self.assertEqual(result["status"], "partial_success")
        self.assertEqual(result["provider_errors"][0]["provider"], "FMP")
        self.assertEqual(len(result["treasury_yield_proxies"]), 4)

    def test_treasury_fmp_empty_and_yahoo_error_is_complete_failure(self):
        fmp = Mock(api_key="fixture")
        fmp.get_treasury_rates.return_value = []
        result = self.treasury([{"error": "HTTP 429"}], fmp=fmp)
        self.assertEqual(result["status"], "error")
        self.assertEqual({e["provider"] for e in result["provider_errors"]}, {"FMP", "Yahoo"})

    def test_treasury_fmp_partial_rows_retained_with_valid_proxies(self):
        fmp = Mock(api_key="fixture")
        fmp.get_treasury_rates.return_value = [{"date": "2026-10-05", "year10": 4.1}, {"error": "provider failed"}]
        result = self.treasury(self.quotes(), fmp=fmp)
        self.assertEqual(result["status"], "partial_success")
        self.assertEqual(result["treasury_rates"], [{"date": "2026-10-05", "year10": 4.1}])
        self.assertEqual(len(result["treasury_yield_proxies"]), 4)

    def test_treasury_fmp_valid_has_same_schema(self):
        fmp = Mock(api_key="fixture")
        rows = [{"date": "2026-10-05", "year10": 4.1}]
        fmp.get_treasury_rates.return_value = rows
        result = self.treasury([], fmp=fmp)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["treasury_rates"], rows)
        self.assertEqual(result["treasury_yield_proxies"], [])
        self.assertEqual(set(result), set(self.treasury(self.quotes())))

    def test_invalid_numeric_quote_is_not_success(self):
        for price in (None, True, "4", math.nan, math.inf):
            with self.subTest(price=price):
                self.assertEqual(self.treasury([{"symbol": "^IRX", "price": price}])["status"], "error")

    def test_auth_config_file_precedes_environment_and_requires_auth(self):
        from deployment.sandbox_auth import sandbox_api_key
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "server.toml"
            source.write_text('[server]\napi_key = "fixture-file-key"\n')
            self.assertEqual(sandbox_api_key({"OPEN_SANDBOX_CONFIG_FILE": str(source), "OPEN_SANDBOX_API_KEY": "fixture-env-key"}), "fixture-file-key")
            source.write_text('[server]\napi_key = ""\n')
            with self.assertRaises(ValueError):
                sandbox_api_key({"OPEN_SANDBOX_CONFIG_FILE": str(source), "OPEN_SANDBOX_API_KEY": "fixture-env-key"})
        with self.assertRaises(ValueError):
            sandbox_api_key({})

    def test_server_renders_authenticated_config_from_same_source(self):
        from deployment.start_server import server_config
        import tomllib
        config = server_config({"OPEN_SANDBOX_API_KEY": "fixture-key", "OPEN_SANDBOX_EXECD_IMAGE": "fixture-image"})
        self.assertEqual(tomllib.loads(config)["server"]["api_key"], "fixture-key")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "server.toml"
            text = '[server]\napi_key = "fixture-file-key"\n[egress]\nmode = "dns+nft"\n'
            source.write_text(text)
            self.assertEqual(server_config({"OPEN_SANDBOX_CONFIG_FILE": str(source), "OPEN_SANDBOX_API_KEY": "different-fixture-key"}), text)

    def test_explicit_file_credential_is_redacted_in_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "server.toml"
            source.write_text('[server]\napi_key = "fixture-file-secret"\n')
            with patch.dict(os.environ, {"OPEN_SANDBOX_CONFIG_FILE": str(source), "OPEN_SANDBOX_API_KEY": "different-fixture-secret"}), patch.object(sb.SandboxSync, "create", side_effect=RuntimeError("401 fixture-file-secret")):
                manager = sb.SandboxManager("fake")
                manager.start()
                self.assertEqual(manager.error, "401 [redacted]")

    def test_client_and_server_resolve_same_key_and_sdk_header(self):
        from deployment.sandbox_auth import sandbox_api_key
        import httpx
        from opensandbox.sync.manager import SandboxManagerSync
        from opensandbox.models.sandboxes import SandboxFilter
        seen = []
        def handle(request):
            seen.append(request.headers.get("OPEN-SANDBOX-API-KEY"))
            return httpx.Response(200, json={"items": [], "pagination": {"page": 1, "pageSize": 10, "totalItems": 0, "totalPages": 0, "hasNextPage": False}})
        with patch.dict(os.environ, {"OPEN_SANDBOX_CONFIG_FILE": "", "OPEN_SANDBOX_API_KEY": "fixture-env-key"}):
            manager = sb.SandboxManager("fake")
            server_key = sandbox_api_key(os.environ)
            config = manager.connection_config.model_copy(update={"transport": httpx.MockTransport(handle)})
            sdk = SandboxManagerSync.create(connection_config=config)
            try:
                sdk.list_sandbox_infos(SandboxFilter())
            finally:
                sdk.close()
        self.assertEqual(seen, [server_key])

    def test_backend_initializes_dependencies_and_closes_them(self):
        backend = importlib.import_module("app")
        from langchain_core.messages import AIMessage
        from langgraph.checkpoint.postgres import PostgresSaver
        from langgraph.store.postgres import PostgresStore
        events = []
        store, saver, manager = Mock(), Mock(), Mock()
        agent = Mock()
        agent.invoke.return_value = {"messages": [AIMessage(content="Diagnostic request succeeded")]}
        @contextmanager
        def store_context(*args, **kwargs):
            events.append("store-open")
            try: yield store
            finally: events.append("store-close")
        @contextmanager
        def saver_context(*args, **kwargs):
            events.append("saver-open")
            try: yield saver
            finally: events.append("saver-close")
        manager.stop.side_effect = lambda: events.append("sandbox-stop")
        def run(application, **kwargs):
            with TestClient(application) as client:
                response = client.post("/api/chat", json={"message": "diagnostic", "thread_id": "test", "org_id": "org-a", "user_id": "test-user"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["reply"], "Diagnostic request succeeded")
        with patch.object(PostgresStore, "from_conn_string", side_effect=store_context), patch.object(PostgresSaver, "from_conn_string", side_effect=saver_context), patch.object(backend, "init_sandbox_manager", return_value=manager), patch.object(backend, "get_sandbox_manager", return_value=manager), patch.object(backend, "create_agent", return_value=agent) as build, patch.object(backend, "_ensure_agents_memory"), patch.object(backend, "_remove_per_user_agents_memory"), patch.object(routes, "_ensure_user_habits"), patch.object(routes, "get_sandbox_manager", return_value=None):
            run(backend.app)
            build.assert_called_once_with(saver, store)
            store.setup.assert_called_once()
            saver.setup.assert_called_once()
        self.assertEqual(events[-3:], ["sandbox-stop", "saver-close", "store-close"])
        self.assertIsNone(routes._agent)

    def test_backend_startup_failure_releases_persistence(self):
        backend = importlib.import_module("app")
        from langgraph.checkpoint.postgres import PostgresSaver
        from langgraph.store.postgres import PostgresStore
        store_cm, saver_cm, manager = Mock(), Mock(), Mock()
        store_cm.__enter__ = Mock(return_value=Mock())
        store_cm.__exit__ = Mock(return_value=False)
        saver_cm.__enter__ = Mock(return_value=Mock())
        saver_cm.__exit__ = Mock(return_value=False)
        def run(application, **kwargs):
            with TestClient(application):
                pass
        with patch.object(PostgresStore, "from_conn_string", return_value=store_cm), patch.object(PostgresSaver, "from_conn_string", return_value=saver_cm), patch.object(backend, "init_sandbox_manager", return_value=manager), patch.object(backend, "create_agent", side_effect=RuntimeError("fixture startup failure")):
            with self.assertRaisesRegex(RuntimeError, "fixture startup failure"):
                run(backend.app)
        manager.stop.assert_called_once()
        saver_cm.__exit__.assert_called_once()
        store_cm.__exit__.assert_called_once()
        self.assertIsNone(routes._agent)

    def test_separate_ui_uses_configured_backend(self):
        import ast
        source = (ROOT / "static" / "app.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        assignment = next(node for node in module.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "BACKEND_BASE_URL" for target in node.targets))
        expression = ast.Expression(assignment.value)
        with patch.dict(os.environ, {"BACKEND_URL": "http://127.0.0.1:18001"}):
            self.assertEqual(eval(compile(expression, "static/app.py", "eval"), {"os": os}), "http://127.0.0.1:18001")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(eval(compile(expression, "static/app.py", "eval"), {"os": os}), "http://localhost:8000")


if __name__ == "__main__":
    unittest.main()
