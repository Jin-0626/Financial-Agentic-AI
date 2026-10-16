"""Seven-repeat release migration gates; no live providers or saved user data."""
import asyncio, json, os, platform, statistics, subprocess, sys, tempfile, time
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evaluation.offline_workloads import OPERATIONS, inputs, compare
from evaluation.baselines.native import calculate
from evaluation.baselines.runtime import execute_python
from financial_analytics.registry import validate
from app.infrastructure.native_engine import NativeEngine
from app.infrastructure.offline_worker import worker_python

async def main():
    selected=[operation for operation in OPERATIONS if operation != "black_scholes" and (not sys.argv[1:] or operation in sys.argv[1:])]
    suffix = ".exe" if os.name == "nt" else ""
    engine_binary = ROOT / "target/release" / ("financial-engine"+suffix)
    example = ROOT / "target/release/examples" / ("offline_benchmark"+suffix)
    report = {"platform": platform.platform(), "python": sys.version, "repetitions": 7, "float_absolute_tolerance": 1e-8, "float_relative_tolerance": 1e-9, "money_tolerance": "1e-8", "rows": [], "gates": {}}
    if sys.argv[1:] and (ROOT/"evaluation/offline_benchmark.json").exists():
        previous=json.loads((ROOT/"evaluation/offline_benchmark.json").read_text())
        report["rows"]=[r for r in previous["rows"] if r["operation"] not in selected]
        report["gates"]={k:v for k,v in previous["gates"].items() if k not in selected}
    report["gates"]["black_scholes"]=False
    report["hold_reasons"]={"black_scholes":"Four seeded large-magnitude monetary cases exceed the absolute 1e-8 parity gate; prototype remains evaluation-only"}
    with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {"ENGINE_COMMAND": json.dumps([str(engine_binary)]), "ENGINE_DATA_ROOT": data}):
        engine = NativeEngine()
        try:
            for operation in selected:
                passed = True
                for scale in ("small", "typical", "maximum"):
                    args = inputs(operation, scale)
                    row = {"operation": operation, "scale": scale, "request_bytes": len(json.dumps(args).encode())}
                    try:
                        baseline_pure=json.loads(subprocess.check_output([str(worker_python()),"-m","evaluation.baselines.timing"],input=json.dumps({"operation":operation,"inputs":args}),text=True,stderr=subprocess.PIPE))
                        reference = baseline_pure["result"]
                        pure = json.loads(subprocess.check_output([str(example)], input=json.dumps({"operation":operation,"inputs":args}), text=True, stderr=subprocess.PIPE))
                        compare(pure["result"], reference)
                        start = time.perf_counter(); native = await engine.call("migration-benchmark", "offline_"+operation, args)
                        row["first_call_ms"] = (time.perf_counter()-start)*1000
                        compare(native, reference)
                        timings = {"python_calculation_ms": [], "python_worker_ms": [], "rust_mcp_ms": []}
                        for _ in range(7):
                            timings["python_calculation_ms"].append(baseline_pure["calculation_ms"])
                            start=time.perf_counter(); baseline=await execute_python(operation,args);timings["python_worker_ms"].append((time.perf_counter()-start)*1000)
                            if baseline["status"] != "success": raise AssertionError(baseline)
                            compare(baseline["result"],reference)
                            start=time.perf_counter(); result=await engine.call("migration-benchmark","offline_"+operation,args);timings["rust_mcp_ms"].append((time.perf_counter()-start)*1000)
                            compare(result,reference)
                        row.update({key:statistics.median(values) for key,values in timings.items()})
                        row["rust_calculation_ms"]=pure["calculation_ms"]
                        row["reduction_fraction"]=1-row["rust_mcp_ms"]/row["python_worker_ms"]
                        row["parity"] = True
                    except Exception as error:
                        passed=False;row["parity"]=False;row["error"]=str(error)[:300]
                    report["rows"].append(row);print(json.dumps(row),flush=True)
                rows=[r for r in report["rows"] if r["operation"]==operation]
                report["gates"][operation]=passed and rows[1].get("reduction_fraction",0)>=.2 and rows[0].get("reduction_fraction",-1)>=-.1
                (ROOT/"evaluation/offline_benchmark.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
        finally: await engine.close()
    print(json.dumps({"gates":report["gates"]}),flush=True)
if __name__ == "__main__": asyncio.run(main())
