"""Independent numerical and protocol contracts for native offline candidates."""
import asyncio, json, os, tempfile, unittest
from unittest.mock import patch
from app.infrastructure.native_engine import NativeEngine
from evaluation.offline_workloads import OPERATIONS, inputs, compare
from evaluation.baselines.native import calculate
from financial_analytics.registry import validate
from evaluation.baselines.runtime import execute_python

@unittest.skipUnless(os.getenv("ENGINE_TEST_COMMAND"), "Set ENGINE_TEST_COMMAND")
class OfflineRustTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.environment=patch.dict(os.environ,{"ENGINE_COMMAND":os.environ["ENGINE_TEST_COMMAND"],"ENGINE_DATA_ROOT":self.directory.name})
        self.environment.start();self.engine=NativeEngine()
    async def asyncTearDown(self):
        await self.engine.close();self.environment.stop();self.directory.cleanup()
    async def test_all_operations_match_independent_reference(self):
        for operation in (op for op in OPERATIONS if op != "black_scholes"):
            for scale in ("small","typical","maximum"):
                with self.subTest(operation=operation,scale=scale):
                    args=inputs(operation,scale)
                    result=await self.engine.call("parity", "offline_"+operation,args)
                    baseline=await execute_python(operation,args)
                    self.assertEqual(baseline["status"],"success")
                    compare(result,baseline["result"])
    async def test_flat_complete_loss_negative_rate_and_missing_denominators(self):
        cases=[("relative_strength_index",{"prices":[100.0]*30}), ("bollinger_bands",{"prices":[100.0]*30}), ("portfolio_performance",{"returns":[0.0]*30,"benchmark":[0.0]*30}), ("portfolio_performance",{"returns":[-1.0]+[0.0]*29}), ("historical_risk",{"returns":[.01]*30}), ("historical_risk",{"returns":[1e300,2e300]+[1e300]*28}), ("portfolio_performance",{"returns":[-1.0,1e300]+[0.0]*28}), ("net_present_value",{"discount_rate":-.001}), ("statement_ratios",{"revenue":"0","equity":"0","current_liabilities":"0"})]
        for operation,changes in cases:
            with self.subTest(operation=operation,changes=changes):
                args=validate(operation,{**inputs(operation,"small"),**changes}).model_dump(mode="json")
                compare(await self.engine.call("edges","offline_"+operation,args),calculate(operation,validate(operation,args)))
    async def test_invalid_and_concurrent_calls(self):
        args=inputs("moving_average","small")
        for change in ({"window":1},{"prices":[1,2]},{"unknown":1},{"window":True},{"prices":[0]*30}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                await self.engine.call("invalid","offline_moving_average",{**args,**change})
        results=await asyncio.gather(*(self.engine.call("concurrent","offline_moving_average",args) for _ in range(8)))
        self.assertTrue(all(r==results[0] for r in results))

    async def test_held_pricing_tool_is_not_exposed(self):
        tools=await self.engine.tools("held")
        self.assertNotIn("offline_black_scholes",tools)

    async def test_promoted_tool_envelope_uses_real_native_adapter(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from orchestrator.tools.analytics import run_analytics
        from financial_analytics.registry import NATIVE_OFFLINE_OPERATIONS
        runtime=SimpleNamespace(context=SimpleNamespace(org_id="real-envelope"))
        with patch("app.infrastructure.native_engine.calculate",side_effect=self.engine.call):
            for operation in NATIVE_OFFLINE_OPERATIONS:
                with self.subTest(operation=operation):
                    args=inputs(operation,"small")
                    if "currency" in args:args["currency"]="MYR"
                    result=json.loads(await run_analytics.coroutine(operation,args,runtime))
                    self.assertEqual(result["status"],"success")
                    self.assertEqual(result["operation"],operation)
                    self.assertEqual(result["provenance"]["engine"],"rust")
                    self.assertEqual(result["provenance"]["currency"],args.get("currency"))
                    reference=await execute_python(operation,args)
                    compare(result["result"],reference["result"])
