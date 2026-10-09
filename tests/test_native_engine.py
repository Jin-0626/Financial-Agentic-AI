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
        cls.env=dict(os.environ, ENGINE_DATA_ROOT=cls.temp.name, ENGINE_ORG_ID="native-test", OTEL_ENABLED="false")
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
        self.assertEqual(len(listing),9)
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
    def test_existing_python_ratio_parity(self):
        from datetime import date
        from scripts.financialanalysis.data_processor import DataProcessor, DataSource, CompanyInfo, FinancialPeriod, ReportingStandard
        company=CompanyInfo("0157.KL","Synthetic","","","",ReportingStandard.UNKNOWN,"12-31","MYR")
        period=FinancialPeriod(date(2024,12,31),"annual",2024,"FY")
        accounts={"revenue":1000,"gross_profit":400,"net_income":-50,"current_assets":200,"current_liabilities":100}
        legacy=DataProcessor().process_data(accounts,DataSource.API,company,period)
        native=self.call("extract_financial_ratios",{"ticker":"0157.KL","period":"2024-12-31"})["result"]["structuredContent"]["data"]["ratios"]
        self.assertEqual(legacy.ratios["gross_margin_pct"],40)
        self.assertEqual(legacy.ratios["net_margin_pct"],-5)
        self.assertEqual(legacy.ratios["current_ratio"],2)
        for name,value in legacy.ratios.items(): self.assertAlmostEqual(float(native[name]["value"]),value)
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

    def test_missing_and_malformed_trace_context_remain_nonfatal(self):
        args={"ticker":"0157.KL","lookback_days":4,"confidence_level":0.95}
        for id,meta in enumerate([{}, {"traceparent":"malformed"}, {"traceparent":123},
                                  {"traceparent":"00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01","tracestate":"bad"}],2):
            result=self.session.request(id,"tools/call",{"name":"calculate_historical_var","arguments":args,"_meta":meta})
            self.assertFalse(result["result"]["isError"],result)

    def test_internal_worker_cooperative_cancel(self):
        worker=Session(self.command("worker"),self.env)
        try:
            job={"name":"run_monte_carlo_simulation","arguments":{"ticker":"0157.KL","simulations":100000,"horizon_days":2520,"seed":42,"lookback_days":4},"snapshot_id":self.ids["prices"],"traceparent":None,"tracestate":None}
            worker.raw(json.dumps(job).encode()+b"\n"+b'{"cancel":true}\n')
            result=worker.receive()
            self.assertTrue(result["is_error"],result)
            self.assertEqual(result["result"]["error"]["code"],"cancelled")
        finally: worker.close()
