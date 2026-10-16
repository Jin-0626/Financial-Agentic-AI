"""Native contract tests. Set ENGINE_TEST_COMMAND to a JSON argv template.

Placeholders: {data_root}, {timeout_ms}, {container_name}. A template ends with
financial-engine; this suite appends import/schema/serve. CI uses the local binary.
"""
from __future__ import annotations
import json
import os
import queue
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
import statistics
from datetime import datetime, timezone

COMMAND = os.environ.get("ENGINE_TEST_COMMAND")

class Session:
    def __init__(self, command, env):
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, env=env)
        self.messages = queue.Queue()
        self.errors = bytearray()
        def read():
            for line in self.process.stdout:
                try: self.messages.put(json.loads(line))
                except Exception as exc: self.messages.put(exc)
        def errors():
            for line in self.process.stderr: self.errors.extend(line)
        self.reader = threading.Thread(target=read, daemon=True); self.reader.start()
        self.error_reader = threading.Thread(target=errors, daemon=True); self.error_reader.start()
    def write(self, value):
        self.raw(json.dumps(value, allow_nan=False).encode()+b"\n")
    def raw(self, value):
        self.process.stdin.write(value); self.process.stdin.flush()
    def receive(self, seconds=15):
        value = self.messages.get(timeout=seconds)
        if isinstance(value, Exception): raise value
        return value
    def request(self, id, method, params=None):
        value={"jsonrpc":"2.0","id":id,"method":method}
        if params is not None: value["params"]=params
        self.write(value); result=self.receive()
        if result.get("id") != id: raise AssertionError(result)
        return result
    def close(self):
        self.process.stdin.close()
        try: self.process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            self.process.kill(); self.process.wait(timeout=5)
            raise AssertionError("Native shutdown exceeded deadline")
        self.reader.join(timeout=1); self.error_reader.join(timeout=1)
        self.process.stdout.close(); self.process.stderr.close()

@unittest.skipUnless(COMMAND, "ENGINE_TEST_COMMAND is required for native integration")
class NativeEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix="native-engine-")
        cls.env=dict(os.environ, ENGINE_DATA_ROOT=cls.temp.name, ENGINE_ORG_ID="native-test")
        cls.base=json.loads(COMMAND)
        cls.ids={}
        common={"schema_version":1,"org_id":"native-test","ticker":"0157.KL","currency":"MYR",
                "unit_multiplier":"1","provider":"independent-fixture","source_reference":"reviewed fixture",
                "retrieved_at":datetime.now(timezone.utc).isoformat()}
        datasets={
            "prices": ("2025-01-05", {"kind":"prices","bars":[{"date":f"2025-01-0{i+1}","adjusted_close":price,"volume":0}
                       for i,price in enumerate([100,110,99,108.9,98.01])]}),
            "statements": ("2024-12-31", {"kind":"statements","periods":[{"period":"2024-12-31","annual":True,
                         "accounts":{"revenue":"1000","gross_profit":"400","net_income":"-50","current_assets":"200","current_liabilities":"100"}}]}),
            "forecast": ("2024-12-31", {"kind":"forecast","forecast":{"valuation_date":"2024-12-31",
                        "cash_flows":[{"date":"2025-12-31","amount":"100"}],"net_debt":"50","diluted_shares":"10"}}),
        }
        for kind,(as_of,dataset) in datasets.items():
            body=dict(common,as_of=as_of,dataset=dataset)
            outcome=subprocess.run(cls.command("import"),input=json.dumps(body).encode(),capture_output=True,env=cls.env,timeout=30)
            if outcome.returncode: raise AssertionError(f"Import failed: {outcome.stderr.decode()}")
            cls.ids[kind]=json.loads(outcome.stdout)["snapshot_id"]
    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()
    @classmethod
    def command(cls, mode, timeout=30000, name=None):
        name=name or "native-"+uuid.uuid4().hex[:12]
        return [arg.replace("{data_root}",cls.temp.name).replace("{timeout_ms}",str(timeout)).replace("{container_name}",name)
                for arg in cls.base]+[mode]
    def setUp(self):
        self.session=Session(self.command("serve"), self.env)
        result=self.session.request(1,"initialize",{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"native-test","version":"1"}})
        self.assertEqual(result["result"]["protocolVersion"],"2025-11-25")
        self.session.write({"jsonrpc":"2.0","method":"notifications/initialized"})
    def tearDown(self): self.session.close()
    def call(self,name,args,id=2):
        return self.session.request(id,"tools/call",{"name":name,"arguments":args})
    def test_four_tools_schemas_and_independent_results(self):
        listing=self.session.request(2,"tools/list")["result"]["tools"]
        self.assertEqual(len(listing),25)
        self.assertIn("binomial_option", {item["name"] for item in listing})
        for tool in listing: self.assertFalse(tool["inputSchema"]["additionalProperties"])
        risk=self.call("calculate_historical_var",{"ticker":"0157.KL","lookback_days":4,"confidence_level":0.75},3)
        self.assertFalse(risk["result"]["isError"],risk)
        data=risk["result"]["structuredContent"]
        self.assertAlmostEqual(data["data"]["historical"]["var"],0.1)
        self.assertEqual(data["provenance"]["currency"],"MYR")
        ratios=self.call("extract_financial_ratios",{"ticker":"0157.KL","period":"2024-12-31"},4)
        self.assertEqual(ratios["result"]["structuredContent"]["data"]["ratios"]["net_margin_pct"]["value"],"-5.00")
        dcf=self.call("compute_discounted_cash_flow",{"ticker":"0157.KL","wacc":0.1,"terminal_growth_rate":0,"historical_years":1,"forecast_id":self.ids["forecast"]},5)
        self.assertFalse(dcf["result"]["isError"],dcf)
        self.assertAlmostEqual(float(dcf["result"]["structuredContent"]["data"]["equity_value"]),950,places=5)
    def test_backtest_python_parity_and_invalid_arguments(self):
        import math
        from datetime import date, timedelta
        from decimal import Decimal
        from financial_analytics.registry import validate
        from evaluation.baselines.native import calculate
        for strategy in ("buy_and_hold", "moving_average_crossover", "rsi"):
            for count in (30, 1000, 10000):
                args = validate("offline_backtest", {"prices": [100 + 12 * math.sin(i / 9) + i * 0.002 for i in range(count)],
                    "dates": [(date(2000, 1, 1) + timedelta(days=i)).isoformat() for i in range(count)],
                    "strategy": strategy, "initial_cash": "10000", "cost_bps": 15.5, "currency": "MYR"})
                expected = calculate("offline_backtest", args)
                response = self.call("offline_backtest", args.model_dump(mode="json"), 80)["result"]
                self.assertFalse(response["isError"], response)
                actual = response["structuredContent"]
                with self.subTest(strategy=strategy, count=count):
                    self.assertEqual(actual["assumptions"], expected["assumptions"])
                    self.assertEqual(len(actual["trades"]), len(expected["trades"]))
                    for a, b in zip(actual["trades"], expected["trades"]):
                        self.assertEqual((a["date"], a["side"]), (b["date"], b["side"]))
                        for field in ("quantity", "price", "cost"):
                            self.assertLessEqual(abs(Decimal(a[field]) - Decimal(b[field])), Decimal("0.00000001"))
                    for a, b in zip(actual["equity"], expected["equity"]):
                        self.assertEqual(a["date"], b["date"])
                        self.assertLessEqual(abs(Decimal(a["equity"]) - Decimal(b["equity"])), Decimal("0.00000001"))
                    self.assertLessEqual(abs(Decimal(actual["final_equity"]) - Decimal(expected["final_equity"])), Decimal("0.00000001"))
                    self.assertAlmostEqual(actual["total_return"], expected["total_return"], places=12)
                    self.assertEqual(Decimal(actual["metrics"]["total_costs"]), sum((Decimal(trade["cost"]) for trade in expected["trades"]), Decimal(0)))
                    peak = Decimal(expected["initial_cash"]); drawdown = Decimal(0)
                    for row in expected["equity"]:
                        value = Decimal(row["equity"]); peak = max(peak, value); drawdown = min(drawdown, value / peak - 1)
                    self.assertAlmostEqual(actual["metrics"]["maximum_drawdown"], float(drawdown), places=12)
        valid = args.model_dump(mode="json")
        for change in ({"prices": [1, 0, 3]}, {"strategy": "code"}, {"cost_bps": -1}, {"leverage": 2},
                       {"dates": ["2025-01-01"] * 10000}, {"fast_window": 30, "slow_window": 20}):
            response = self.call("offline_backtest", {**valid, **change}, 81)["result"]
            self.assertTrue(response["isError"], response)

    def test_backtest_decimal_and_rsi_degenerate_parity(self):
        from decimal import Decimal
        from financial_analytics.registry import validate
        from evaluation.baselines.native import calculate
        base = {"prices": [100, 101, 102, 103, 104], "dates": ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04", "2025-01-05"]}
        scenarios = [{"initial_cash": "1e3", "cost_bps": 0}, {"initial_cash": "1000.1234567890", "cost_bps": 1000},
                     {"strategy": "rsi", "rsi_window": 2, "prices": [100] * 5},
                     {"strategy": "rsi", "rsi_window": 2, "prices": [104, 103, 102, 101, 100]},
                     {"strategy": "moving_average_crossover", "fast_window": 2, "slow_window": 3}]
        for change in scenarios:
            args = validate("offline_backtest", {**base, **change})
            expected = calculate("offline_backtest", args)
            actual = self.call("offline_backtest", args.model_dump(mode="json"), 85)["result"]
            with self.subTest(change=change):
                self.assertFalse(actual["isError"], actual)
                result = actual["structuredContent"]
                self.assertLessEqual(abs(Decimal(result["final_equity"]) - Decimal(expected["final_equity"])), Decimal("0.00000001"))
                self.assertEqual(result["trades"], expected["trades"])

    def test_backtest_oversized_result_returns_error_without_closing_connection(self):
        from datetime import date, timedelta
        from financial_analytics.registry import validate
        count = 10000
        args = validate("offline_backtest", {"strategy": "rsi", "rsi_window": 2, "lower": 49, "upper": 51,
            "cost_bps": 0, "prices": [100 if i % 2 else 100.0001 for i in range(count)],
            "dates": [(date(2000, 1, 1) + timedelta(days=i)).isoformat() for i in range(count)]})
        result = self.call("offline_backtest", args.model_dump(mode="json"), 83)["result"]
        self.assertTrue(result["isError"], result)
        self.assertEqual(result["structuredContent"]["error"]["code"], "result_too_large")
        self.assertEqual(self.session.request(84, "ping")["result"], {})

    def test_binomial_python_parity_and_invalid_arguments(self):
        from decimal import Decimal
        from financial_analytics.registry import validate
        from evaluation.baselines.native import calculate
        base={"spot":"100","strike":"95","years":1,"rate":0.05,"dividend_yield":0.02,"volatility":0.2,"side":"call","steps":100}
        scenarios=[base,{**base,"side":"put"},{**base,"years":0},{**base,"rate":-0.01},
                   {**base,"steps":1000},{**base,"strike":"120","volatility":0.5}]
        for index,args in enumerate(scenarios,20):
            with self.subTest(args=args):
                expected=calculate("binomial_option",validate("binomial_option",args))["price"]
                result=self.call("binomial_option",args,index)["result"]
                self.assertFalse(result["isError"],result)
                price=result["structuredContent"]["price"]
                self.assertIsInstance(price,str)
                self.assertLessEqual(abs(Decimal(price)-Decimal(expected)),Decimal("0.00000001"))
        for index,args in enumerate(({**base,"steps":0},{**base,"unexpected":"private-input"},
                                    {**base,"steps":1,"volatility":0.0001},
                                    {**base,"years":30,"volatility":5,"steps":2000}),40):
            result=self.call("binomial_option",args,index)["result"]
            self.assertTrue(result["isError"],result)
            self.assertNotIn("private-input",json.dumps(result))

    def test_financial_ratios_known_values(self):
        from datetime import date
        native=self.call("extract_financial_ratios",{"ticker":"0157.KL","period":"2024-12-31"})["result"]["structuredContent"]["data"]["ratios"]
        for name, value in {"gross_margin_pct": 40, "net_margin_pct": -5, "current_ratio": 2}.items():
            self.assertAlmostEqual(float(native[name]["value"]), value)
        self.assertIsNone(native["ebitda_margin_pct"]["value"])

    def test_seeded_simulation(self):
        args={"ticker":"0157.KL","simulations":1000,"horizon_days":20,"seed":42,"lookback_days":4}
        a=self.call("run_monte_carlo_simulation",args,3)
        b=self.call("run_monte_carlo_simulation",args,4)
        self.assertFalse(a["result"]["isError"],a)
        self.assertEqual(a["result"],b["result"])
    def test_domain_errors_and_unknown_arguments(self):
        for id,args in enumerate([{"ticker":"0157.KL","lookback_days":4,"confidence_level":1},
                                  {"ticker":"0157.KL","lookback_days":4,"confidence_level":0.95,"secret":"never-record-this"},
                                  {"ticker":"0157.KL","lookback_days":4,"confidence_level":0.95,"snapshot_id":"../../private"},
                                  {"ticker":"AAPL","lookback_days":4,"confidence_level":0.95}],2):
            result=self.call("calculate_historical_var",args,id)
            self.assertTrue(result["result"]["isError"],result)
            self.assertNotIn("never-record-this",json.dumps(result))
    def test_protocol_noise_frame_limit_and_notifications(self):
        self.session.raw(b'{not json}\n')
        self.assertEqual(self.session.receive()["error"]["code"],-32700)
        self.session.raw(b'{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"calculate_historical_var","arguments":{"wacc":1,"wacc":2}}}\n')
        self.assertEqual(self.session.receive()["error"]["code"],-32700)
        self.session.raw(b'x'*1048577+b'\n')
        self.assertEqual(self.session.receive()["error"]["code"],-32700)
        self.session.write({"jsonrpc":"2.0","method":"unknown-notification"})
        self.assertEqual(self.session.request(10,"ping")["result"],{})
        self.assertEqual(self.call("unknown_tool",{},11)["error"]["code"],-32602)
    def test_concurrent_correlation_and_cancel(self):
        args={"ticker":"0157.KL","simulations":100000,"horizon_days":2520,"seed":42,"lookback_days":4}
        self.session.write({"jsonrpc":"2.0","id":20,"method":"tools/call","params":{"name":"run_monte_carlo_simulation","arguments":args}})
        self.session.write({"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":20}})
        self.session.write({"jsonrpc":"2.0","id":21,"method":"ping"})
        responses={r["id"]:r for r in [self.session.receive(),self.session.receive()]}
        self.assertEqual(responses[21]["result"],{})
        self.assertEqual(responses[20]["result"]["structuredContent"]["error"]["code"],"cancelled")
        for id in range(30,62): self.session.write({"jsonrpc":"2.0","id":id,"method":"ping"})
        self.assertEqual({self.session.receive()["id"] for _ in range(32)},set(range(30,62)))
    def test_untrusted_import_denied(self):
        body={"schema_version":1,"org_id":"other-org"}
        result=subprocess.run(self.command("import"),input=json.dumps(body).encode(),capture_output=True,env=self.env,timeout=30)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(result.stdout,b"")

    def test_deadline_releases_workers(self):
        limited_env=dict(self.env, ENGINE_COMPUTE_TIMEOUT_MS="1")
        limited=Session(self.command("serve",timeout=1),limited_env)
        try:
            limited.request(1,"initialize",{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"deadline-test","version":"1"}})
            limited.write({"jsonrpc":"2.0","method":"notifications/initialized"})
            args={"ticker":"0157.KL","simulations":100000,"horizon_days":2520,"seed":42,"lookback_days":4}
            reply=limited.request(2,"tools/call",{"name":"run_monte_carlo_simulation","arguments":args})
            self.assertEqual(reply["result"]["structuredContent"]["error"]["code"],"worker_timeout")
            self.assertEqual(limited.request(3,"ping")["result"],{})
        finally: limited.close()

    def test_bounded_admission_and_cancellation_load(self):
        args={"ticker":"0157.KL","simulations":100000,"horizon_days":2520,"seed":42,"lookback_days":4}
        payload=b"".join(json.dumps({"jsonrpc":"2.0","id":id,"method":"tools/call","params":{"name":"run_monte_carlo_simulation","arguments":args}}).encode()+b"\n" for id in range(100,196))
        self.session.raw(payload)
        for id in range(100,196): self.session.write({"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":id}})
        results=[self.session.receive(30) for _ in range(96)]
        self.assertEqual({result["id"] for result in results},set(range(100,196)))
        errors=[result["result"]["structuredContent"].get("error",{}).get("code") for result in results]
        self.assertIn("overloaded",errors)
        self.assertIn("cancelled",errors)
        self.assertEqual(self.session.request(200,"ping")["result"],{})

    def test_ping_latency_reference(self):
        values=[]
        for id in range(1000,1100):
            start=time.perf_counter()
            self.session.request(id,"ping")
            values.append((time.perf_counter()-start)*1000)
        p95=sorted(values)[94]
        print(json.dumps({"native_ping_p95_ms":round(p95,3),"samples":100,"platform":os.name}),flush=True)
        # The 20ms gate is evaluated on a documented four-CPU local container.
        if os.environ.get("ENGINE_BENCHMARK_GATE") == "1": self.assertLess(p95,20)

    @unittest.skipUnless(COMMAND and '"docker"' in COMMAND, "Docker worker-kill verification")
    def test_worker_kill_returns_error_then_recovers(self):
        name="native-kill-"+uuid.uuid4().hex[:10]
        isolated=Session(self.command("serve",name=name),self.env)
        killer=None
        try:
            isolated.request(1,"initialize",{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"worker-kill-test","version":"1"}})
            isolated.write({"jsonrpc":"2.0","method":"notifications/initialized"})
            # Only children of this test's dedicated engine container are targeted.
            code='for n in $(seq 1 500); do for pid in $(cat /proc/1/task/*/children); do kill -KILL "$pid"; exit 0; done; sleep 0.01; done; exit 1'
            killer=subprocess.Popen(["docker","exec",name,"sh","-c",code],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            args={"ticker":"0157.KL","simulations":100000,"horizon_days":2520,"seed":42,"lookback_days":4}
            response=isolated.request(2,"tools/call",{"name":"run_monte_carlo_simulation","arguments":args})
            killer.communicate(timeout=10)
            self.assertEqual(killer.returncode,0)
            self.assertEqual(response["result"]["structuredContent"]["error"]["code"],"worker_crash")
            response=isolated.request(3,"tools/call",{"name":"calculate_historical_var","arguments":{"ticker":"0157.KL","lookback_days":4,"confidence_level":0.95}})
            self.assertFalse(response["result"]["isError"],response)
        finally:
            if killer and killer.poll() is None: killer.kill(); killer.communicate()
            isolated.close()

    def test_optional_mcp_metadata_remains_nonfatal(self):
        args={"ticker":"0157.KL","lookback_days":4,"confidence_level":0.95}
        for id,meta in enumerate([{}, {"custom":"value"}, {"custom":123}],2):
            result=self.session.request(id,"tools/call",{"name":"calculate_historical_var","arguments":args,"_meta":meta})
            self.assertFalse(result["result"]["isError"],result)

    def test_internal_worker_cooperative_cancel(self):
        worker=Session(self.command("worker"),self.env)
        try:
            job={"name":"run_monte_carlo_simulation","arguments":{"ticker":"0157.KL","simulations":100000,"horizon_days":2520,"seed":42,"lookback_days":4},"snapshot_id":self.ids["prices"]}
            worker.raw(json.dumps(job).encode()+b"\n"+b'{"cancel":true}\n')
            result=worker.receive()
            self.assertTrue(result["is_error"],result)
            self.assertEqual(result["result"]["error"]["code"],"cancelled")
        finally: worker.close()
