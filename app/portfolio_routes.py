"""Portfolio APIs are separate from the chatbot and blocked in production until authenticated."""

import asyncio
import os
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query
from app import portfolio_service as service, providers
from app.diagnostics import sanitize_error
from app.auth import authenticated_identity, organization, user, configured

_repository = None


def set_repository(repo):
    global _repository
    _repository = repo


def enabled():
    if (
        os.getenv("APP_ENV", "local").lower() == "production" and not configured()
    ) or os.getenv("PORTFOLIO_ENABLED", "true").lower() != "true":
        raise HTTPException(404, "Portfolio feature is disabled")


router = APIRouter(
    prefix="/portfolio",
    tags=["portfolio"],
    dependencies=[Depends(enabled), Depends(authenticated_identity)],
)


def repository():
    if _repository is None:
        raise HTTPException(503, "Portfolio storage unavailable")
    return _repository


Org = Annotated[str, Depends(organization)]
User = Annotated[str, Depends(user)]
Pid = Annotated[str, Query(pattern=r"^[a-zA-Z0-9_-]{1,64}$")]
Revision = Annotated[int | None, Query(ge=0)]


async def action(awaitable):
    try:
        return await awaitable
    except ValueError as error:
        raise HTTPException(409, sanitize_error(error)) from None
    except (RuntimeError, OSError) as error:
        raise HTTPException(503, sanitize_error(error)) from None


@router.get("")
async def load(org_id: Org, user_id: User, portfolio_id: Pid = "default"):
    return await service.load_portfolio(
        repository(), org_id, user_id, portfolio_id
    ) or {"portfolio": None}


@router.put("")
async def save(
    portfolio: service.Portfolio,
    org_id: Org,
    user_id: User,
    portfolio_id: Pid = "default",
    revision: Revision = None,
):
    return await action(
        service.save_portfolio(
            repository(), org_id, user_id, portfolio, portfolio_id, revision
        )
    )


@router.get("/list")
async def list_saved(org_id: Org, user_id: User):
    return {"portfolios": await service.list_portfolios(repository(), org_id, user_id)}


@router.post("/create")
async def create(portfolio: service.Portfolio, org_id: Org, user_id: User):
    return await action(
        service.create_portfolio(repository(), org_id, user_id, portfolio)
    )


@router.get("/symbols")
async def symbols(query: Annotated[str, Query(min_length=1, max_length=80)]):
    try:
        return {"symbols": await asyncio.to_thread(providers.search_symbols, query)}
    except Exception as error:
        raise HTTPException(502, sanitize_error(error)) from None


@router.post("/transactions")
async def transaction(
    trade: service.Trade,
    org_id: Org,
    user_id: User,
    portfolio_id: Pid = "default",
    revision: Revision = None,
):
    doc = await service.load_portfolio(repository(), org_id, user_id, portfolio_id)
    if doc is None:
        raise HTTPException(404, "Portfolio not found")
    try:
        item = await asyncio.to_thread(providers.quote, trade.symbol)
        if item["currency"] != doc["portfolio"]["currency"]:
            raise ValueError("Trade currency differs; FX conversion is not available")
    except Exception as error:
        raise HTTPException(422, sanitize_error(error)) from None
    return await action(
        service.record_trade(
            repository(), org_id, user_id, portfolio_id, trade, revision
        )
    )


@router.put("/transactions/{transaction_id}")
async def correct_transaction(
    transaction_id: str,
    correction: service.TradeCorrection,
    org_id: Org,
    user_id: User,
    revision: Annotated[int, Query(ge=0)],
    portfolio_id: Pid = "default",
):
    return await action(
        service.correct_trade(
            repository(),
            org_id,
            user_id,
            portfolio_id,
            transaction_id,
            correction,
            revision,
        )
    )


@router.get("/summary")
async def summary(org_id: Org, user_id: User, portfolio_id: Pid = "default"):
    return await action(
        service.saved_summary(repository(), org_id, user_id, portfolio_id)
    )


@router.delete("")
async def delete(
    org_id: Org, user_id: User, portfolio_id: Pid = "default", revision: Revision = None
):
    await action(
        service.delete_portfolio(repository(), org_id, user_id, portfolio_id, revision)
    )
    return {"deleted": portfolio_id}


@router.get("/export")
async def export(org_id: Org, user_id: User, portfolio_id: Pid = "default"):
    return await action(
        service.export_json(repository(), org_id, user_id, portfolio_id)
    )


@router.post("/import/preview")
async def preview(
    request: service.ImportRequest,
    org_id: Org,
    user_id: User,
    portfolio_id: Pid = "default",
):
    return await action(
        service.import_preview(repository(), org_id, user_id, portfolio_id, request)
    )


@router.post("/import/commit")
async def commit(
    request: service.ImportRequest,
    org_id: Org,
    user_id: User,
    portfolio_id: Pid = "default",
):
    return await action(
        service.import_commit(repository(), org_id, user_id, portfolio_id, request)
    )
