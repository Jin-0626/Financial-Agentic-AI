import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.backtesting.routes import router


@unittest.skipUnless(
    os.getenv("ENGINE_TEST_COMMAND"), "ENGINE_TEST_COMMAND is required for native API integration"
)
class BacktestingNativeApiTests(unittest.TestCase):
    def test_api_returns_rust_result_through_real_adapter(self):
        from contextlib import asynccontextmanager
        import tempfile
        from app.infrastructure.native_engine import NativeEngine

        with (
            tempfile.TemporaryDirectory(prefix="backtest-api-") as root,
            patch.dict(
                os.environ,
                {
                    "ENGINE_COMMAND": os.environ["ENGINE_TEST_COMMAND"],
                    "ENGINE_DATA_ROOT": root,
                    "APP_ENV": "local",
                    "OIDC_ISSUER": "",
                    "OIDC_AUDIENCE": "",
                    "OIDC_JWKS_URL": "",
                    "OIDC_CLIENT_ID": "",
                },
            ),
        ):
            engine = NativeEngine()

            @asynccontextmanager
            async def lifespan(app):
                try:
                    yield
                finally:
                    await engine.close()

            app = FastAPI(lifespan=lifespan)
            app.include_router(router, prefix="/api")
            with (
                patch("app.backtesting.service.calculate", side_effect=engine.call),
                TestClient(app) as client,
            ):
                response = client.post(
                    "/api/backtesting/run?org_id=api-fixture",
                    json={
                        "prices": [100, 110, 90],
                        "dates": ["2025-01-01", "2025-01-02", "2025-01-03"],
                        "initial_cash": "1000",
                        "cost_bps": 0,
                        "currency": "MYR",
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                self.assertIn("api-fixture", engine.connections)
                self.assertEqual(payload["provenance"]["engine"], "rust")
                self.assertEqual(payload["provenance"]["currency"], "MYR")
                self.assertEqual(payload["result"]["final_equity"], "818.18181818")
                self.assertEqual(payload["result"]["metrics"]["trade_count"], 1)
                self.assertEqual(payload["result"]["trades"][0]["date"], "2025-01-02")
