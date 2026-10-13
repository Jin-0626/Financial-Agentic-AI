"""Authenticated backtesting screen; execution uses the bounded Rust worker."""
import asyncio
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from financial_analytics.registry import strategy_catalog
from financial_analytics.schemas import Backtest
from app.backtesting.service import execute_backtest
from app.infrastructure.auth import authenticated_identity, organization
from app.infrastructure.diagnostics import sanitize_error
import app.market.common as market_data_common
from app.market.service import market_service

router = APIRouter(prefix="/backtesting", dependencies=[Depends(authenticated_identity)])


class HistoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: Annotated[str, Field(pattern=r"^[A-Za-z0-9.^=_-]{1,32}$")]
    start: date
    end: date

    @model_validator(mode="after")
    def dates_valid(self):
        if self.start >= self.end or (self.end - self.start).days > 36525:
            raise ValueError("Choose an increasing date range of at most 100 years")
        if self.end == date.max:
            raise ValueError("End date is out of range")
        return self


def load_history(args):
    ticker, info = market_data_common.ticker_info(args.symbol.upper())
    frame = ticker.history(start=args.start.isoformat(), end=(args.end + timedelta(days=1)).isoformat(),
                           interval="1d", auto_adjust=True, actions=False, timeout=10)
    rows = []
    for timestamp, row in frame.iterrows():
        close = market_data_common.number(row.get("Close"))
        if close is None or close <= 0:
            raise ValueError("History contains invalid closing prices; use verified CSV data")
        rows.append({"date": timestamp.strftime("%Y-%m-%d"), "close": close})
    if not 3 <= len(rows) <= 10000:
        raise ValueError("Choose a date range with 3 to 10,000 daily observations")
    if any(a["date"] >= b["date"] for a, b in zip(rows, rows[1:])):
        raise ValueError("History dates are not unique and increasing")
    currency = info.get("currency")
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha():
        raise ValueError("Provider did not supply the price currency; use verified CSV data")
    return market_data_common.result(rows, "Yahoo Finance", "Adjusted daily closing prices; dividends and splits reflected in prices",
                              currency=currency.upper(), symbol=args.symbol.upper(), interval="1d")


@router.get("/catalog")
async def catalog():
    return {"engine": "Rust", "strategies": strategy_catalog(),
            "limits": {"observations": 10000, "deadline_seconds": 60},
            "execution": "Prior-close signals execute at the next close; fractional shares, long-only"}


@router.post("/history")
async def history(args: HistoryInput):
    return await market_service.get(("backtest_history", args.symbol.upper(), args.start.isoformat(), args.end.isoformat()),
                                   "yahoo", 300, lambda: load_history(args))


@router.post("/run")
async def run(args: Backtest, request: Request, org_id: str = Depends(organization)):
    task = asyncio.create_task(execute_backtest(org_id, args.model_dump(mode="json")))
    try:
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=0.1)
            if not done and await request.is_disconnected():
                raise asyncio.CancelledError()
        response = await task
        if response.get("status") != "success":
            raise HTTPException(422, sanitize_error(response.get("error", "Backtest failed")))
        return response
    except TimeoutError:
        raise HTTPException(504, "Backtest deadline exceeded; reduce the data size") from None
    except (ValueError, OSError, RuntimeError) as error:
        raise HTTPException(503, sanitize_error(error)) from None
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
