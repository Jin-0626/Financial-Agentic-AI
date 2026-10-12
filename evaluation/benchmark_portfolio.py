"""Deterministic portfolio timings; no live providers or application database.

Run from the repository root: python -B evaluation/benchmark_portfolio.py
Requires cargo +1.99.0 build --release --locked and
cargo +1.99.0 build --release --locked -p quant-engine --example portfolio_benchmark.
Use --baseline to load import_preview from HEAD without changing working files.
"""
import asyncio
import json
import os
import platform
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
import pandas as pd
from app import portfolio_service as service, providers
from app.native_engine import NativeEngine
from app.portfolio_repository import MemoryPortfolioRepository


def measure(fn, repeats=5):
    fn()
    elapsed = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        elapsed.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(elapsed), 3)


async def timed(fn, repeats=5):
    await fn()
    elapsed = []
    for _ in range(repeats):
        start = time.perf_counter()
        await fn()
        elapsed.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(elapsed), 3)


QUOTE_TIMINGS = []
QUOTE_LOCK = threading.Lock()


def quote(symbol):
    start = time.perf_counter()
    time.sleep(.01)
    end = time.perf_counter()
    with QUOTE_LOCK:
        QUOTE_TIMINGS.append((start, end))
    return {"symbol": symbol, "currency": "USD", "price": "100"}


def provider_wall(symbols):
    intervals = QUOTE_TIMINGS[-symbols:]
    elapsed = max(end for _, end in intervals) - min(start for start, _ in intervals)
    return round(elapsed * 1000, 3)


def request(count, symbols):
    return service.ImportRequest(data={
        "portfolio_name": "Benchmark",
        "currency": "USD",
        "transactions": [
            {"id": f"t{i}", "symbol": f"S{i % symbols}", "type": "BUY",
             "quantity": "1", "price": "100", "date": "2025-01-01"}
            for i in range(count)
        ],
    })


def rust_compute(name, arguments):
    filename = "portfolio_benchmark.exe" if os.name == "nt" else "portfolio_benchmark"
    executable = ROOT / "target/release/examples" / filename
    payload = json.dumps({"name": name, "arguments": arguments}).encode()
    raw = subprocess.check_output([str(executable)], input=payload)
    return json.loads(raw)["rust_compute_median_ms"]


async def main():
    if "--baseline" in sys.argv:
        source = subprocess.check_output(["git", "show", "HEAD:app/portfolio_service.py"]).decode()
        namespace = {"__name__": "portfolio_baseline"}
        exec(compile(source, "portfolio_baseline", "exec"), namespace)
        namespace["ledger"] = service.ledger
        service.import_preview = namespace["import_preview"]
    engine_path = ROOT / "target/release" / ("financial-engine.exe" if os.name == "nt" else "financial-engine")
    if not engine_path.is_file():
        raise SystemExit("Build the release engine before benchmarking")
    rows = {"method": "median of 5 warmed runs (3 for previews); provider mock sleeps 10 ms/call", "python": sys.version,
            "engine": str(engine_path), "platform": platform.platform(),
            "source_revision": subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip(),
            "baseline": "--baseline" in sys.argv,
            "normalization": [], "imports": [], "summaries": []}
    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
        "ENGINE_COMMAND": json.dumps([str(engine_path)]), "ENGINE_DATA_ROOT": directory}):
        engine = NativeEngine()
        start = time.perf_counter()
        await engine.tools("benchmark")
        rows["connection_startup_ms"] = round((time.perf_counter()-start)*1000, 3)
        try:
            start = time.perf_counter()
            await engine.call("benchmark", "portfolio_summary", {
                "currency": "USD", "positions": [], "quotes": [],
            })
            rows["first_native_call_ms"] = round((time.perf_counter() - start) * 1000, 3)
            for symbols in (10, 100):
                names = [f"S{i}" for i in range(symbols)]
                for points in (252, 2520):
                    frame = pd.DataFrame({(s, "Close"): [100.0+i/100 for i in range(points)] for s in names},
                                         index=pd.date_range("2015-01-01", periods=points))
                    with patch.object(providers.yf, "download", return_value=frame):
                        fn = lambda: providers.price_series(names, dict.fromkeys(names, "USD"), "5y")
                        value = fn()
                        normalization = measure(fn)
                        extraction = measure(lambda: [[str(p) for p in frame[s]["Close"]] for s in names])
                        serialization = measure(lambda: json.loads(json.dumps(value)))
                    payload_bytes = len(json.dumps(value).encode())
                    rows["normalization"].append({
                        "symbols": symbols, "points": points,
                        "python_normalization_ms": normalization,
                        "dataframe_value_extraction_ms": extraction,
                        "json_roundtrip_ms": serialization,
                        "normalized_json_bytes": payload_bytes,
                        "normalized_json_under_1mib": payload_bytes < 1_048_576,
                    })
            for symbols in (10, 50):
                portfolio = service.Portfolio(positions=[{"symbol": f"S{i}", "quantity": "1", "average_cost": "90"} for i in range(symbols)])
                args = {"currency": "USD", "sectors": {}, "positions": [p.model_dump(mode="json") for p in portfolio.positions],
                        "quotes": [{"symbol": p.symbol, "currency": "USD", "price": "100"} for p in portfolio.positions]}
                native_ms = await timed(lambda: engine.call("benchmark", "portfolio_summary", args))
                with patch.object(service, "calculate", engine.call), patch.object(providers, "quote", quote):
                    total = await timed(lambda: service.snapshot("benchmark", portfolio))
                rows["summaries"].append({
                    "symbols": symbols,
                    "native_mcp_worker_ms": native_ms,
                    "summary_with_provider_wait_ms": total,
                    "provider_wait_wall_ms": provider_wall(symbols),
                    "rust_compute_ms": rust_compute("portfolio_summary", args),
                    "json_roundtrip_ms": measure(lambda: json.loads(json.dumps(args))),
                })
            repo = MemoryPortfolioRepository()
            for count in (100, 2000):
                for symbols in (10, 50):
                    req = request(count, symbols)
                    trades = service.import_trades(req.data)
                    args = {"opening_positions": [], "transactions": trades}
                    native_ms = await timed(lambda: engine.call("benchmark", "portfolio_ledger", args))
                    with patch.object(service, "calculate", engine.call), patch.object(providers, "quote", quote):
                        total = await timed(lambda: service.import_preview(repo, "benchmark", "user", None, req), 3)
                    rows["imports"].append({
                        "transactions": count, "symbols": symbols,
                        "python_validation_ms": measure(lambda: service.import_trades(req.data)),
                        "json_roundtrip_ms": measure(lambda: json.loads(json.dumps(args))),
                        "native_mcp_worker_ms": native_ms,
                        "rust_compute_ms": rust_compute("portfolio_ledger", args),
                        "preview_with_provider_wait_ms": total,
                        "provider_wait_wall_ms": provider_wall(symbols),
                        "sequential_provider_wait_floor_ms": symbols * 10,
                    })
        finally:
            await engine.close()
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
