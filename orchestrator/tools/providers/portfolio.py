from __future__ import annotations
import json, logging
from langchain_core.tools import BaseTool, tool
from langchain.tools import ToolRuntime
from app.infrastructure.diagnostics import sanitize_error
from . import common
logger = logging.getLogger(__name__)

def _make_portfolio_tool() -> BaseTool:
    import asyncio

    @tool
    async def portfolio_analytics(
        action: str, payload_json: str, runtime: ToolRuntime
    ) -> str:
        """Use Rust for weighted-average transaction replay or explicitly labelled fixed-holdings projection.

        replay_transactions accepts Financial transactions (date,symbol,type,quantity,price).
        current_holdings_nav accepts {positions:[{symbol,quantity,average_cost?}],period,benchmark?}.
        Results are calculations, not stored user portfolios. No cash, fees, taxes or FX.
        """
        from app.infrastructure.native_engine import calculate
        from app.portfolio.service import import_trades, Position
        import app.infrastructure.providers as providers

        org = getattr(runtime.context, "org_id", "default-org")
        try:
            if len(payload_json) > 1048576:
                raise ValueError("Portfolio payload exceeds 1 MiB")
            parsed = json.loads(payload_json)
            if action == "replay_transactions":
                trades = import_trades(
                    {"portfolio_name": "Calculation", "transactions": parsed}
                )
                result = await calculate(
                    org,
                    "portfolio_ledger",
                    {"opening_positions": [], "transactions": trades},
                )
                return common._to_json(
                    {
                        **result,
                        "method": "Decimal weighted-average ledger; no historical NAV inferred",
                    }
                )
            if action == "current_holdings_nav":
                if not isinstance(parsed, dict) or not isinstance(
                    parsed.get("positions"), list
                ):
                    raise ValueError("Supply positions")
                positions = [
                    Position.model_validate({"average_cost": "0", **p}).model_dump(
                        mode="json"
                    )
                    for p in parsed["positions"]
                ]
                if not 1 <= len(positions) <= 50:
                    raise ValueError("Use between 1 and 50 holdings")
                benchmark = providers.symbol(
                    parsed.get("benchmark", positions[0]["symbol"])
                )
                symbols = list(
                    dict.fromkeys([p["symbol"] for p in positions] + [benchmark])
                )
                quotes = await asyncio.gather(
                    *(asyncio.to_thread(providers.quote, s) for s in symbols)
                )
                currencies = {q["symbol"]: q["currency"] for q in quotes}
                if len(set(currencies.values())) != 1:
                    raise ValueError("Mixed currencies; FX conversion unavailable")
                period = parsed.get("period", "6mo")
                if period not in {"1mo", "3mo", "6mo", "1y", "2y", "5y"}:
                    raise ValueError("Unsupported period")
                series = await asyncio.to_thread(
                    providers.price_series, symbols, currencies, period
                )
                return common._to_json(
                    await calculate(
                        org,
                        "portfolio_performance",
                        {
                            "currency": quotes[0]["currency"],
                            "benchmark": benchmark,
                            "method": "projection",
                            "positions": positions,
                            "series": series,
                        },
                    )
                )
            raise ValueError("Use replay_transactions or current_holdings_nav")
        except Exception as error:
            return common._to_json(
                {
                    "status": "error",
                    "error": sanitize_error(error),
                    "category": "calculation",
                }
            )

    return portfolio_analytics
