import asyncio
import os
import unittest
from decimal import Decimal
from unittest.mock import patch, Mock

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.backtesting.routes import router, HistoryInput, load_history, run
from financial_analytics.schemas import Backtest


class BacktestingTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(
            os.environ,
            {
                "APP_ENV": "local",
                "OIDC_ISSUER": "",
                "OIDC_AUDIENCE": "",
                "OIDC_JWKS_URL": "",
                "OIDC_CLIENT_ID": "",
            },
        )
        env.start()
        self.addCleanup(env.stop)
        app = FastAPI()
        app.include_router(router, prefix="/api")
        self.client = TestClient(app)
        self.inputs = {
            "prices": [100, 110, 90],
            "dates": ["2025-01-02", "2025-01-03", "2025-01-06"],
            "strategy": "buy_and_hold",
            "initial_cash": "1000",
            "cost_bps": 0,
            "currency": "USD",
        }

    def test_native_response_preserves_prior_close_and_metrics(self):
        from tests.support.baselines.native import calculate

        result = calculate("offline_backtest", Backtest.model_validate(self.inputs))
        result["metrics"] = {
            "maximum_drawdown": 90 / 110 - 1,
            "trade_count": 1,
            "open_position": True,
            "total_costs": "0",
        }
        with patch("app.backtesting.service.calculate", return_value=result) as native:
            response = self.client.post("/api/backtesting/run", json=self.inputs)
        native.assert_called_once()
        self.assertEqual(native.call_args.args[1], "offline_backtest")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        result = payload["result"]
        self.assertEqual(result["trades"][0]["date"], "2025-01-03")
        self.assertEqual(result["trades"][0]["price"], "110.00000000")
        self.assertEqual(result["equity"][0]["equity"], "1000.00000000")
        self.assertAlmostEqual(result["total_return"], 90 / 110 - 1)
        self.assertAlmostEqual(result["metrics"]["maximum_drawdown"], 90 / 110 - 1)
        self.assertEqual(result["metrics"]["trade_count"], 1)
        self.assertTrue(result["metrics"]["open_position"])
        self.assertEqual(Decimal(result["metrics"]["total_costs"]), 0)
        self.assertEqual(payload["provenance"]["currency"], "USD")
        self.assertEqual(payload["provenance"]["engine"], "rust")

    def test_catalog_matches_execution_strategies(self):
        payload = self.client.get("/api/backtesting/catalog").json()
        self.assertEqual(
            {s["id"] for s in payload["strategies"]},
            {"buy_and_hold", "rsi", "moving_average_crossover"},
        )
        crossover = next(s for s in payload["strategies"] if s["id"] == "moving_average_crossover")
        self.assertIn("fast_window", crossover["parameters"])
        self.assertIn("slow_window", crossover["parameters"])

    def test_invalid_strategy_dates_and_execution_rejected_before_worker(self):
        invalid = [
            {"strategy": "code"},
            {"dates": ["2025-01-03"] * 3},
            {"prices": [100, 0, 90]},
            {"cost_bps": 1001},
            {"strategy": "moving_average_crossover", "fast_window": 30, "slow_window": 20},
            {"prices": [True, 110, 90]},
            {"leverage": 2},
        ]
        with patch("app.backtesting.routes.execute_backtest") as worker:
            for changes in invalid:
                with self.subTest(changes=changes):
                    self.assertEqual(
                        self.client.post(
                            "/api/backtesting/run", json={**self.inputs, **changes}
                        ).status_code,
                        422,
                    )
            worker.assert_not_called()

    def test_worker_error_and_timeout_are_visible(self):
        with patch(
            "app.backtesting.routes.execute_backtest",
            return_value={"status": "error", "error": "Invalid inputs"},
        ):
            self.assertEqual(
                self.client.post("/api/backtesting/run", json=self.inputs).status_code, 422
            )
        with patch("app.backtesting.routes.execute_backtest", side_effect=TimeoutError):
            self.assertEqual(
                self.client.post("/api/backtesting/run", json=self.inputs).status_code, 504
            )

    def test_provider_history_adjustment_dates_and_currency(self):
        args = HistoryInput(symbol="1155.KL", start="2025-01-01", end="2025-01-06")
        ticker = Mock()
        ticker.history.return_value = pd.DataFrame(
            {"Close": [100, 110, 90]},
            index=pd.DatetimeIndex(
                ["2025-01-02", "2025-01-03", "2025-01-06"], tz="Asia/Kuala_Lumpur"
            ),
        )
        with patch("app.market.common.ticker_info", return_value=(ticker, {"currency": "MYR"})):
            result = load_history(args)
        self.assertEqual(result["data"][0]["date"], "2025-01-02")
        self.assertEqual(result["currency"], "MYR")
        ticker.history.assert_called_once_with(
            start="2025-01-01",
            end="2025-01-07",
            interval="1d",
            auto_adjust=True,
            actions=False,
            timeout=10,
        )
        with patch("app.market.common.ticker_info", return_value=(ticker, {})):
            with self.assertRaisesRegex(ValueError, "currency"):
                load_history(args)

    def test_history_validation_and_production_auth(self):
        self.assertEqual(
            self.client.post(
                "/api/backtesting/history",
                json={"symbol": "http://private", "start": "2025-01-01", "end": "2025-02-01"},
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.post(
                "/api/backtesting/history",
                json={"symbol": "AAPL", "start": "2025-02-01", "end": "2025-01-01"},
            ).status_code,
            422,
        )
        with patch.dict(os.environ, {"APP_ENV": "production"}):
            self.assertEqual(self.client.get("/api/backtesting/catalog").status_code, 503)

    def test_disconnect_cancels_execution_and_drains_worker(self):
        async def scenario():
            cancelled = asyncio.Event()

            async def worker(*args):
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

            class Request:
                async def is_disconnected(self):
                    return True

            with patch("app.backtesting.routes.execute_backtest", side_effect=worker):
                with self.assertRaises(asyncio.CancelledError):
                    await run(Backtest.model_validate(self.inputs), Request())
            self.assertTrue(cancelled.is_set())

        asyncio.run(scenario())
