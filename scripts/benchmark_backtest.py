"""Synthetic offline parity/performance benchmark; no provider requests."""

import asyncio
from datetime import date, timedelta
from decimal import Decimal
import json
import math
import os
import platform
from pathlib import Path
import statistics
import subprocess
import tempfile
import time
from unittest.mock import patch

from financial_analytics.registry import validate
from tests.support.baselines.native import calculate
from tests.support.baselines.runtime import execute_python
from app.infrastructure.native_engine import NativeEngine


ROOT = Path(__file__).resolve().parents[1]


async def main():
    (ROOT / "benchmark-results").mkdir(exist_ok=True)
    suffix = ".exe" if os.name == "nt" else ""
    binary = ROOT / "target/release" / ("financial-engine" + suffix)
    example = ROOT / "target/release/examples" / ("backtest_benchmark" + suffix)
    report = {
        "repeats": 5,
        "platform": platform.platform(),
        "application_python": platform.python_version(),
        "workload": "synthetic offline daily close strategies",
        "monetary_tolerance": "0.00000001",
        "rows": [],
    }
    with (
        tempfile.TemporaryDirectory(prefix="backtest-benchmark-") as data,
        patch.dict(
            os.environ, {"ENGINE_COMMAND": json.dumps([str(binary)]), "ENGINE_DATA_ROOT": data}
        ),
    ):
        engine = NativeEngine()
        try:
            for strategy in ("buy_and_hold", "moving_average_crossover", "rsi"):
                for count in (30, 1000, 10000):
                    args = validate(
                        "offline_backtest",
                        {
                            "strategy": strategy,
                            "currency": "USD",
                            "cost_bps": 15.5,
                            "prices": [
                                100 + 12 * math.sin(i / 9) + i * 0.002 for i in range(count)
                            ],
                            "dates": [
                                (date(2000, 1, 1) + timedelta(days=i)).isoformat()
                                for i in range(count)
                            ],
                        },
                    )
                    inputs = args.model_dump(mode="json")
                    baseline = calculate("offline_backtest", args)
                    pure = json.loads(
                        subprocess.check_output([str(example)], input=json.dumps(inputs), text=True)
                    )
                    if abs(
                        Decimal(pure["result"]["final_equity"]) - Decimal(baseline["final_equity"])
                    ) > Decimal("0.00000001"):
                        raise AssertionError("Numerical parity failed")
                    start = time.perf_counter()
                    await engine.call("benchmark", "offline_backtest", inputs)
                    first = (time.perf_counter() - start) * 1000
                    times = {
                        "python_calculation_ms": [],
                        "python_worker_total_ms": [],
                        "rust_mcp_total_ms": [],
                    }
                    for _ in range(5):
                        start = time.perf_counter()
                        calculate("offline_backtest", args)
                        times["python_calculation_ms"].append((time.perf_counter() - start) * 1000)
                        start = time.perf_counter()
                        python = await execute_python("offline_backtest", inputs)
                        times["python_worker_total_ms"].append((time.perf_counter() - start) * 1000)
                        if python["status"] != "success":
                            raise AssertionError(python)
                        start = time.perf_counter()
                        await engine.call("benchmark", "offline_backtest", inputs)
                        times["rust_mcp_total_ms"].append((time.perf_counter() - start) * 1000)
                    row = {
                        "strategy": strategy,
                        "observations": count,
                        "rust_calculation_ms": pure["calculation_ms"],
                        "rust_first_call_ms": first,
                        **{key: statistics.median(value) for key, value in times.items()},
                    }
                    row["reduction_fraction"] = (
                        1 - row["rust_mcp_total_ms"] / row["python_worker_total_ms"]
                    )
                    report["rows"].append(row)
                    print(json.dumps(row), flush=True)
        finally:
            await engine.close()
    (ROOT / "benchmark-results/backtest_benchmark.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    asyncio.run(main())
