"""Documented LangChain MCPAdapter integration; Rust owns calculations."""

import asyncio
import json
import os
import shutil
from pathlib import Path
from langchain_core.tools import StructuredTool, ToolException
from app.diagnostics import sanitize_error

from orchestrator.tool_registry import NATIVE_TOOLS

QUANT_TOOLS = NATIVE_TOOLS - {"register_forecast"}


class NativeEngine:
    def __init__(self):
        self.connections = {}
        self.lock = asyncio.Lock()
        self.slots = asyncio.Semaphore(4)
        self.closed = False
        default = (
            Path(__file__).resolve().parents[1]
            / "target"
            / "debug"
            / ("financial-engine.exe" if os.name == "nt" else "financial-engine")
        )
        command = [str(default)]
        linux = default.with_suffix("")
        if (
            os.name == "nt"
            and not default.exists()
            and linux.exists()
            and shutil.which("wsl.exe")
        ):
            with linux.open("rb") as stream:
                is_elf = stream.read(4) == bytes([127, 69, 76, 70])
            if is_elf:
                absolute = linux.resolve()
                command = [
                    "wsl.exe",
                    "--",
                    "/mnt/" + absolute.drive[0].lower() + absolute.as_posix()[2:],
                ]
        self.command = json.loads(os.getenv("ENGINE_COMMAND", json.dumps(command)))
        if (
            not isinstance(self.command, list)
            or not self.command
            or not all(isinstance(v, str) and v for v in self.command)
        ):
            raise ValueError("ENGINE_COMMAND must be a JSON argument list")

    async def _connection(self, org_id, ready, stop):
        from langchain.mcp import MCPAdapter

        try:
            async with MCPAdapter(
                {
                    "mcpServers": {
                        "engine": {
                            "command": self.command[0],
                            "args": [*self.command[1:], "serve"],
                            "env": {
                                "ENGINE_ORG_ID": org_id,
                                "ENGINE_DATA_ROOT": str(
                                    Path(
                                        os.getenv("ENGINE_DATA_ROOT", ".engine-data")
                                    ).resolve()
                                ),
                                "ENGINE_COMPUTE_TIMEOUT_MS": "30000",
                                **(
                                    {
                                        "WSLENV": "ENGINE_ORG_ID:ENGINE_DATA_ROOT/p:ENGINE_COMPUTE_TIMEOUT_MS"
                                    }
                                    if Path(self.command[0]).name.lower()
                                    in {"wsl", "wsl.exe"}
                                    else {}
                                ),
                            },
                        }
                    }
                }
            ) as adapter:
                tools = {t.name: t for t in await adapter.list_tools()}
                ready.set_result(tools)
                await stop.wait()
        except BaseException as error:
            if not ready.done():
                ready.set_exception(error)
            elif not isinstance(error, asyncio.CancelledError):
                import logging

                logging.getLogger(__name__).error(
                    "Native connection closed: %s", sanitize_error(error)
                )

    async def tools(self, org_id):
        if not isinstance(org_id, str) or not org_id or len(org_id) > 128:
            raise ValueError("Invalid organization")
        async with self.lock:
            if self.closed:
                raise RuntimeError("Native engine is closed")
            if org_id in self.connections and self.connections[org_id][2].done():
                self.connections.pop(org_id)
            if org_id not in self.connections:
                if len(self.connections) >= 8:
                    raise ValueError("Native organization capacity reached")
                ready = asyncio.get_running_loop().create_future()
                stop = asyncio.Event()
                task = asyncio.create_task(self._connection(org_id, ready, stop))
                self.connections[org_id] = (ready, stop, task)
            ready, _, task = self.connections[org_id]
        try:
            return await asyncio.wait_for(asyncio.shield(ready), 30)
        except BaseException:
            if task.done():
                async with self.lock:
                    # Another caller may already have replaced this failed connection.
                    current = self.connections.get(org_id)
                    if current is not None and current[2] is task:
                        self.connections.pop(org_id)
            raise

    async def call(self, org_id, name, arguments):
        async with self.slots:
            tools = await self.tools(org_id)
            if name not in tools:
                raise ValueError("Native tool unavailable")
            try:
                result = await asyncio.wait_for(tools[name].ainvoke(arguments), 35)
            except ToolException as error:
                raise ValueError(sanitize_error(error)) from None
            if isinstance(result, tuple):
                result = result[0]
            if isinstance(result, list):
                result = "".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in result
                )
            payload = json.loads(result) if isinstance(result, str) else result
            if not isinstance(payload, dict) or payload.get("status") == "error":
                raise ValueError("Native calculation failed")
            return payload

    async def close(self):
        self.closed = True
        items = list(self.connections.values())
        for _, stop, _ in items:
            stop.set()
        if items:
            await asyncio.gather(
                *(task for _, _, task in items), return_exceptions=True
            )
        self.connections.clear()

    async def agent_tools(self, org_id):
        native = await self.tools(org_id)
        tools = []
        for name in sorted(QUANT_TOOLS):
            source = native[name]

            async def execute(_name=name, **kwargs):
                try:
                    kind = (
                        "statements"
                        if _name
                        in {"compute_discounted_cash_flow", "extract_financial_ratios"}
                        else "prices"
                    )
                    if not kwargs.get("snapshot_id"):
                        from orchestrator.tools.native_snapshots import (
                            fetch_yahoo_snapshot,
                        )

                        snapshot = await asyncio.to_thread(
                            fetch_yahoo_snapshot,
                            kind,
                            org_id=org_id,
                            ticker=kwargs["ticker"],
                            lookback_days=kwargs.get("lookback_days", 252),
                        )
                        registered = await self.call(
                            org_id, "register_snapshot", snapshot
                        )
                        kwargs["snapshot_id"] = registered["snapshot_id"]
                    result = await self.call(org_id, _name, kwargs)
                    return json.dumps(result, allow_nan=False)
                except Exception as error:
                    return json.dumps(
                        {
                            "status": "error",
                            "error": sanitize_error(error),
                            "category": "calculation",
                        }
                    )

            tools.append(
                StructuredTool(
                    name=name,
                    description=source.description,
                    args_schema=source.args_schema,
                    coroutine=execute,
                    handle_tool_error=True,
                )
            )

        async def register_forecast(
            ticker: str,
            currency: str,
            valuation_date: str,
            cash_flows: list[dict],
            net_debt: str,
            diluted_shares: str,
            assumptions: str,
        ):
            """Register an explicit dated DCF forecast with stated assumptions; never infer future cash flows from past statements."""
            from datetime import datetime, timezone

            if not assumptions.strip() or len(assumptions) > 2000:
                raise ValueError("State bounded forecast assumptions")
            from app.providers import symbol

            snapshot = {
                "schema_version": 1,
                "org_id": org_id,
                "ticker": symbol(ticker),
                "currency": currency,
                "unit_multiplier": "1",
                "provider": "explicit-forecast",
                "source_reference": "user-supplied-forecast",
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "as_of": valuation_date,
                "dataset": {
                    "kind": "forecast",
                    "forecast": {
                        "valuation_date": valuation_date,
                        "cash_flows": cash_flows,
                        "net_debt": net_debt,
                        "diluted_shares": diluted_shares,
                    },
                },
            }
            return json.dumps(
                {
                    **await self.call(org_id, "register_snapshot", snapshot),
                    "assumptions": assumptions,
                }
            )

        tools.append(
            StructuredTool.from_function(
                coroutine=register_forecast,
                name="register_forecast",
                handle_tool_error=True,
            )
        )
        return tools


_engine = None


def set_engine(engine):
    global _engine
    _engine = engine


async def calculate(org_id, name, arguments):
    if _engine is None:
        raise RuntimeError("Native engine unavailable; build the Rust workspace")
    return await _engine.call(org_id, name, arguments)
