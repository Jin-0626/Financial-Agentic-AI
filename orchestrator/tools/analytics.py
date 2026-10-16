"""Deep Agents access to reviewed offline analytics and existing native tools."""
import asyncio
import json
import os
import subprocess
import threading
from pathlib import Path
from langchain.tools import tool, ToolRuntime
from app.infrastructure.config import PROJECT_ROOT
from app.infrastructure.diagnostics import sanitize_error
from financial_analytics.registry import OPERATIONS, catalog, validate

from app.infrastructure.offline_worker import execute_worker

async def execute_python(operation, inputs, timeout=60):
    if OPERATIONS[operation].native_tool:
        raise ValueError("Operation requires the Rust engine")
    return await execute_worker(operation, inputs, "financial_analytics.worker", timeout)


@tool
def analytics_catalog(category: str | None = None, operation: str | None = None) -> str:
    """Discover reviewed offline operations. Supply an operation to obtain its exact input schema. Unlisted operations cannot execute."""
    try:
        return json.dumps({"operations":catalog(category,operation),"data_policy":"caller-supplied data, workspace private, no live trading"})
    except ValueError as error:
        return json.dumps({"status":"error","category":"validation","error":str(error)})


@tool
async def run_analytics(operation: str, inputs: dict, runtime: ToolRuntime) -> str:
    """Run an operation from analytics_catalog using its exact validated inputs. Never accepts code, module names, paths, or live orders."""
    try:
        validated=validate(operation,inputs).model_dump(mode="json")
        specification=OPERATIONS[operation]
        if operation == "offline_backtest":
            from app.backtesting.service import execute_backtest
            result = await execute_backtest(runtime.context.org_id, validated)
        elif specification.native_tool:
            from app.infrastructure.native_engine import calculate
            if specification.native_tool.startswith("offline_"):
                currency = validated.get("currency")
                native = await calculate(runtime.context.org_id, specification.native_tool, validated)
                result = {"status":"success","operation":operation,"result":native,
                          "provenance":{"engine":"rust","method_version":"financial-analytics-v1",
                                        "data_source":"caller-supplied observations","offline":True,"currency":currency,
                                        "limitations":["Monetary units are unspecified when currency is absent; do not infer an exchange rate"]}}
            elif operation == "binomial_option":
                currency = validated.pop("currency", None)
                native = await calculate(runtime.context.org_id,specification.native_tool,validated)
                result = {"status":"success","operation":operation,"result":native,
                          "provenance":{"engine":"rust","method_version":"financial-analytics-v1",
                                        "data_source":"caller-supplied observations","offline":True,"currency":currency}}
            else:
                result=await calculate(runtime.context.org_id,specification.native_tool,validated)
        else:
            result=await execute_python(operation,validated)
    except TimeoutError:
        result={"status":"error","category":"runtime","error":"Analytics deadline exceeded; reduce the workload"}
    except (ValueError,OSError,RuntimeError) as error:
        result={"status":"error","category":"validation","error":sanitize_error(error)}
    return json.dumps(result,allow_nan=False)
