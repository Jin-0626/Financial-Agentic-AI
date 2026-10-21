"""Research and markets use one application, identity boundary and SQLite lifecycle."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from fastapi.testclient import TestClient
import app.bootstrap.application as application
from app.bootstrap.market_application import app
from app.research.resources import resources


class SharedBackendTests(unittest.TestCase):
    def test_runtime_storage_failures_return_sanitized_503(self):
        from fastapi import FastAPI
        from types import SimpleNamespace
        from app.research.routes import router

        async def checkpoints(*args, **kwargs):
            raise RuntimeError("private database location")
            yield

        application = FastAPI()
        application.include_router(router, prefix="/api")
        environment = {
            "APP_ENV": "local",
            "OIDC_ISSUER": "",
            "OIDC_AUDIENCE": "",
            "OIDC_JWKS_URL": "",
            "OIDC_CLIENT_ID": "",
        }
        agent = Mock(aget_state=AsyncMock(side_effect=RuntimeError("private database location")))
        checkpointer = SimpleNamespace(
            alist=checkpoints,
            adelete_thread=AsyncMock(side_effect=RuntimeError("private database location")),
        )
        with (
            patch.dict(os.environ, environment),
            patch.object(resources, "agent", agent),
            patch.object(resources, "checkpointer", checkpointer),
            TestClient(application) as client,
        ):
            for method, path in (
                ("get", "/api/threads"),
                ("get", "/api/history/shared"),
                ("delete", "/api/threads/shared"),
            ):
                with self.subTest(method=method, path=path):
                    response = getattr(client, method)(path)
                    self.assertEqual(response.status_code, 503)
                    self.assertNotIn("private database location", response.text)
                    self.assertIn("Retry", response.json()["detail"])

    def test_unavailable_research_routes_return_service_unavailable(self):
        from fastapi import FastAPI
        from app.research.routes import router

        application = FastAPI()
        application.include_router(router, prefix="/api")
        environment = {
            "APP_ENV": "local",
            "OIDC_ISSUER": "",
            "OIDC_AUDIENCE": "",
            "OIDC_JWKS_URL": "",
            "OIDC_CLIENT_ID": "",
        }
        with (
            patch.dict(os.environ, environment),
            patch.object(resources, "agent", None),
            patch.object(resources, "checkpointer", None),
            TestClient(application) as client,
        ):
            for method, path in (
                ("get", "/api/threads"),
                ("get", "/api/history/shared"),
                ("delete", "/api/threads/shared"),
            ):
                with self.subTest(method=method, path=path):
                    self.assertEqual(getattr(client, method)(path).status_code, 503)
            for path in ("/api/chat", "/api/chat/stream"):
                self.assertEqual(client.post(path, json={"message": "Question"}).status_code, 503)

    def test_research_and_market_routes_share_the_same_backend(self):
        self.assertIs(app, application.app)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = {
                "APP_ENV": "local",
                "RESEARCH_ENABLED": "true",
                "PORTFOLIO_ENABLED": "true",
                "PORTFOLIO_SQLITE_PATH": str(root / "portfolio.sqlite3"),
                "RESEARCH_SQLITE_PATH": str(root / "research.sqlite3"),
            }
            environment.update(
                {
                    name: ""
                    for name in ("OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL", "OIDC_CLIENT_ID")
                }
            )
            engine = Mock(close=AsyncMock())

            async def stream(*args, **kwargs):
                yield "data: " + json.dumps({"type": "done", "result": "Shared stream"}) + "\n\n"

            with (
                patch.dict(os.environ, environment),
                patch.object(application, "NativeEngine", return_value=engine),
                patch.object(application, "AgentRuntime", return_value=Mock()) as runtime,
                patch.object(application, "_ensure_agents_memory", AsyncMock()),
                patch(
                    "app.market.routes.data_quotes.quote",
                    return_value={"status": "ok", "data": {"price": "3.10"}},
                ),
                patch(
                    "app.research.routes.service._chat",
                    AsyncMock(return_value={"reply": "Shared backend"}),
                ) as chat,
                patch("app.research.routes.streaming._stream_sse", stream),
            ):
                with TestClient(app) as client:
                    self.assertTrue(client.get("/api/health").json()["readiness"]["research"])
                    self.assertTrue(
                        client.get("/api/market/capabilities").json()["features"]["research"]
                    )
                    self.assertEqual(
                        client.get("/api/market/quote?instrument=yahoo:5326.KL").status_code, 200
                    )
                    response = client.post(
                        "/api/chat", json={"message": "Question", "thread_id": "shared"}
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["reply"], "Shared backend")
                    self.assertEqual(chat.await_args.args[1], "default-org__local-user__shared")
                    response = client.post(
                        "/api/chat/stream", json={"message": "Question", "thread_id": "shared"}
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('"result": "Shared stream"', response.text)
                    self.assertEqual(client.get("/api/threads").json(), [])
                    self.assertIs(runtime.call_args.args[2], engine)
                self.assertIsNone(resources.agent)
                engine.close.assert_awaited_once()
            self.assertTrue((root / "research.sqlite3").exists())

    def test_research_failure_keeps_market_and_portfolio_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = {
                "APP_ENV": "local",
                "RESEARCH_ENABLED": "true",
                "PORTFOLIO_SQLITE_PATH": str(root / "portfolio.sqlite3"),
                "RESEARCH_SQLITE_PATH": str(root / "research.sqlite3"),
            }
            environment.update(
                {
                    name: ""
                    for name in ("OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL", "OIDC_CLIENT_ID")
                }
            )
            with (
                patch.dict(os.environ, environment),
                patch.object(application, "NativeEngine", return_value=Mock(close=AsyncMock())),
                patch.object(
                    application,
                    "AgentRuntime",
                    side_effect=RuntimeError("fixture initialization error"),
                ),
                patch.object(application, "_ensure_agents_memory", AsyncMock()),
            ):
                with TestClient(app) as client:
                    health = client.get("/api/health").json()
                    self.assertFalse(health["readiness"]["research"])
                    self.assertTrue(health["readiness"]["storage"])
                    self.assertIn("MODEL_NAME", health["research_error"])
                    self.assertNotIn("fixture initialization error", health["research_error"])
                    self.assertEqual(client.get("/api/portfolio/list").status_code, 200)
                    for method, path in (
                        ("get", "/api/threads"),
                        ("get", "/api/history/shared"),
                        ("delete", "/api/threads/shared"),
                    ):
                        with self.subTest(method=method, path=path):
                            self.assertEqual(getattr(client, method)(path).status_code, 503)

                    self.assertEqual(
                        client.post("/api/chat/stream", json={"message": "Question"}).status_code,
                        503,
                    )
