"""Deterministic CRR benchmark; no provider requests or private workspace data."""
import asyncio
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from financial_analytics.registry import validate
from evaluation.baselines.native import calculate
from evaluation.baselines.runtime import execute_python
from app.infrastructure.native_engine import NativeEngine


def timed(fn):
    start=time.perf_counter(); value=fn()
    return (time.perf_counter()-start)*1000, value


async def timed_async(fn):
    start=time.perf_counter(); value=await fn()
    return (time.perf_counter()-start)*1000, value


async def main():
    exe=ROOT/"target/release/financial-engine.exe"
    example=ROOT/"target/release/examples/option_benchmark.exe"
    os.environ["ENGINE_COMMAND"]=json.dumps([str(exe)])
    with tempfile.TemporaryDirectory(prefix="analytics-benchmark-") as data:
        os.environ["ENGINE_DATA_ROOT"]=data
        engine=NativeEngine()
        report={"repeats":7,"provider_wait_ms":0,"workload":"synthetic European CRR options","rows":[]}
        try:
            for steps in (100,1000,2000):
                inputs={"spot":"100","strike":"100","years":1,"rate":0.05,"dividend_yield":0,"volatility":0.2,"side":"call","steps":steps}
                args=validate("binomial_option",inputs)
                python_start,_=await timed_async(lambda: execute_python("binomial_option",inputs))
                native_start,_=await timed_async(lambda: engine.call("benchmark","binomial_option",inputs))
                values={key:[] for key in ("python_calculation_ms","python_worker_total_ms","json_roundtrip_ms","rust_calculation_ms","rust_mcp_total_ms")}
                for _ in range(7):
                    elapsed, expected=timed(lambda:calculate("binomial_option",args)); values["python_calculation_ms"].append(elapsed)
                    elapsed,_=await timed_async(lambda:execute_python("binomial_option",inputs)); values["python_worker_total_ms"].append(elapsed)
                    elapsed,_=timed(lambda:json.loads(json.dumps(inputs))); values["json_roundtrip_ms"].append(elapsed)
                    pure=json.loads(subprocess.check_output([str(example),str(steps)],text=True)); values["rust_calculation_ms"].append(pure["calculation_ms"])
                    elapsed,result=await timed_async(lambda:engine.call("benchmark","binomial_option",inputs)); values["rust_mcp_total_ms"].append(elapsed)
                    # Pure engine price validates numerical parity independently of RPC envelope.
                    from decimal import Decimal
                    if abs(Decimal(str(pure["price"]))-Decimal(expected["price"]))>Decimal("0.00000001"):
                        raise AssertionError("Rust/Python numerical parity failed")
                row={"steps":steps,**{key:statistics.median(v) for key,v in values.items()},"python_first_call_ms":python_start,"rust_first_call_ms":native_start}
                row["improvement_ms"]=row["python_worker_total_ms"]-row["rust_mcp_total_ms"]
                row["improvement_fraction"]=row["improvement_ms"]/row["python_worker_total_ms"]
                row["threshold_met"]=row["improvement_ms"]>=10 and row["improvement_fraction"]>=0.2
                report["rows"].append(row)
        finally:
            await engine.close()
        target=ROOT/"evaluation/analytics_benchmark.json"
        target.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
        print(json.dumps(report,indent=2))


if __name__=="__main__": asyncio.run(main())
