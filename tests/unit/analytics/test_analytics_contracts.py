import json
import math
import subprocess
import sys
import unittest
from decimal import Decimal
from pathlib import Path

from financial_analytics.calculations import calculate
from evaluation.baselines.native import calculate as reference
from financial_analytics.registry import catalog, strategy_catalog, validate


class AnalyticsContractsTests(unittest.TestCase):
    def test_catalog_matches_execution_schema(self):
        strategies = strategy_catalog()
        operation = catalog(operation="offline_backtest")[0]
        self.assertEqual([item["id"] for item in strategies], operation["input_schema"]["properties"]["strategy"]["enum"])
        self.assertIn("slow_window", strategies[1]["parameters"])
        self.assertNotIn("slow_window", strategies[0]["parameters"])
        self.assertEqual(operation["strategies"], strategies)

    def test_unknown_operations_and_categories_fail_explicitly(self):
        for operation in ("execute_python", [], None):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                validate(operation, {})
        with self.assertRaises(ValueError):
            catalog(operation="not_registered")
        with self.assertRaises(ValueError):
            catalog(category="live_orders")

    def test_reject_invalid_values_and_arbitrary_fields(self):
        for inputs in ({"prices":[1, float("inf"), 3],"window":2},
                       {"prices":[1, True, 3],"window":2},
                       {"prices":[1, 2, 3],"window":2,"path":"secret"}):
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                validate("moving_average", inputs)

    def test_financial_units_and_known_formulas(self):
        args=validate("net_present_value", {"cash_flows":["-100", "110"],"discount_rate":0.1,"currency":"MYR"})
        self.assertEqual(calculate("net_present_value", args)["net_present_value"], "0.00000000")
        self.assertEqual(args.currency, "MYR")
        bond=validate("bond_price_duration", {"coupon_rate":0.05,"yield_rate":0.05,"years":10})
        self.assertEqual(calculate("bond_price_duration",bond)["price"], "1000.00000000")

    def test_option_parity_and_tree_convergence(self):
        inputs={"spot":"100","strike":"100","years":1,"rate":0.05,"volatility":0.2}
        call=Decimal(calculate("black_scholes",validate("black_scholes",inputs))["price"])
        put=Decimal(calculate("black_scholes",validate("black_scholes",{**inputs,"side":"put"}))["price"])
        self.assertAlmostEqual(float(call-put), 100-100*math.exp(-0.05), places=7)
        tree=Decimal(reference("binomial_option",validate("binomial_option",{**inputs,"steps":1000}))["price"])
        self.assertLess(abs(tree-call), Decimal("0.003"))

    def test_next_bar_cost_and_prefix_invariance(self):
        inputs={"prices":[10,20,30,40],"dates":["2026-01-01","2026-01-02","2026-01-03","2026-01-04"],"initial_cash":"1000","cost_bps":100}
        result=reference("offline_backtest",validate("offline_backtest",inputs))
        self.assertEqual(result["trades"][0]["date"],"2026-01-02")
        self.assertEqual(result["trades"][0]["price"],"20.00000000")
        self.assertEqual(result["equity"][0]["equity"],"1000.00000000")
        self.assertLess(Decimal(result["final_equity"]), Decimal("2000"))
        changed=reference("offline_backtest",validate("offline_backtest",{**inputs,"prices":[10,20,30,400]}))
        self.assertEqual(result["equity"][:3],changed["equity"][:3])
        with self.assertRaises(ValueError):
            validate("offline_backtest",{**inputs,"dates":list(reversed(inputs["dates"]))})

    def test_worker_reports_calculation_failure_as_error(self):
        job={"operation":"moving_average","inputs":{"prices":[1,2,3],"window":2,"code":"private-financial-secret"}}
        result=subprocess.run([sys.executable,"-m","financial_analytics.worker"],input=json.dumps(job)+"\n",text=True,capture_output=True,timeout=10,check=True)
        self.assertEqual(json.loads(result.stdout)["status"],"error")
        self.assertNotIn("private-financial-secret",result.stdout + result.stderr)



class AnalyticsNativeRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_backtest_uses_native_service_without_python_worker(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from orchestrator.tools.analytics import run_analytics
        inputs = {"prices": [100, 110, 90], "dates": ["2025-01-01", "2025-01-02", "2025-01-03"], "currency": "MYR"}
        with patch("app.backtesting.service.calculate", new_callable=AsyncMock, return_value={"final_equity": "818.18181818"}) as native, patch("orchestrator.tools.analytics.execute_python", new_callable=AsyncMock) as python:
            result = json.loads(await run_analytics.coroutine("offline_backtest", inputs, SimpleNamespace(context=SimpleNamespace(org_id="private"))))
        native.assert_awaited_once()
        self.assertEqual(native.call_args.args[:2], ("private", "offline_backtest"))
        self.assertEqual(native.call_args.args[2]["currency"], "MYR")
        python.assert_not_awaited()
        self.assertEqual(result["provenance"]["engine"], "rust")
        self.assertEqual(result["provenance"]["currency"], "MYR")

    async def test_native_route_preserves_currency_and_decimal_strings(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from orchestrator.tools.analytics import run_analytics
        runtime=SimpleNamespace(context=SimpleNamespace(org_id="private-workspace"))
        with patch("app.infrastructure.native_engine.calculate",new_callable=AsyncMock) as native:
            native.return_value={"price":"10.45058357","method":"CRR European","steps":100}
            result=json.loads(await run_analytics.coroutine("binomial_option",{"spot":"100","strike":"100","years":1,"currency":"MYR"},runtime))
            self.assertEqual(result["result"]["price"],"10.45058357")
            self.assertEqual(result["provenance"]["currency"],"MYR")
            self.assertEqual(native.call_args.args[0],"private-workspace")
            self.assertNotIn("currency",native.call_args.args[2])

    async def test_unavailable_native_service_is_an_explicit_error(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from orchestrator.tools.analytics import run_analytics
        with patch("app.infrastructure.native_engine.calculate",new_callable=AsyncMock,side_effect=RuntimeError("Native engine unavailable")):
            result=json.loads(await run_analytics.coroutine("binomial_option",{"spot":"100","strike":"100","years":1},SimpleNamespace(context=SimpleNamespace(org_id="private"))))
            self.assertEqual(result["status"],"error")


class OfflineExecutionBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_operations_rejected_by_python_dispatch(self):
        from orchestrator.tools.analytics import execute_python
        with self.assertRaisesRegex(ValueError, "Rust engine"):
            await execute_python("offline_backtest", {})

    async def test_evaluation_worker_retains_independent_backtest_baseline(self):
        from app.infrastructure.offline_worker import execute_worker
        from unittest.mock import patch
        inputs = {"prices": [10, 20, 30], "dates": ["2026-01-01", "2026-01-02", "2026-01-03"], "initial_cash": "1000", "cost_bps": 0}
        with patch("app.infrastructure.offline_worker.worker_python", return_value=Path(sys.executable)):
            response = await execute_worker("offline_backtest", inputs, "evaluation.baselines.worker")
        self.assertEqual(response["status"], "success")
        self.assertEqual(response["result"]["final_equity"], "1500.00000000")
