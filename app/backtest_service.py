"""Shared native execution for the Backtesting screen and analytics tool."""
import asyncio
from financial_analytics.registry import validate
from .native_engine import calculate


async def execute_backtest(org_id, inputs):
    validated = validate("offline_backtest", inputs).model_dump(mode="json")
    async with asyncio.timeout(60):
        result = await calculate(org_id, "offline_backtest", validated)
    return {"status": "success", "operation": "offline_backtest", "result": result,
            "provenance": {"engine": "rust", "method_version": "rust-backtest-v1",
                           "data_source": "caller-supplied observations", "offline": True,
                           "currency": validated.get("currency")}}
