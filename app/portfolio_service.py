"""Financial Deep Agents portfolio workflow: Python persists; Rust calculates."""

import asyncio
import hashlib
import json
from datetime import datetime, timezone, date
from decimal import Decimal
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app import providers
from app.native_engine import calculate
from app.diagnostics import sanitize_error


def now():
    return datetime.now(timezone.utc).isoformat()


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    quantity: Decimal = Field(gt=0, allow_inf_nan=False)
    average_cost: Decimal = Field(ge=0, allow_inf_nan=False)

    @field_validator("symbol")
    @classmethod
    def exact(cls, v):
        return providers.symbol(v)


class Portfolio(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(default="My portfolio", min_length=1, max_length=80)
    owner: str = Field(default="", max_length=128)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    benchmark: str = Field(default="SPY", max_length=32)
    period: Literal["1mo", "3mo", "6mo", "1y", "2y", "5y"] = "1y"
    positions: list[Position] = Field(default_factory=list, max_length=50)

    @field_validator("benchmark")
    @classmethod
    def exact(cls, v):
        return providers.symbol(v)


class Trade(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    symbol: str
    side: Literal["BUY", "SELL", "DIVIDEND"]
    quantity: Decimal = Field(gt=0, allow_inf_nan=False)
    price: Decimal = Field(ge=0, allow_inf_nan=False)
    date: date
    notes: str = Field(default="", max_length=1000)

    @field_validator("symbol")
    @classmethod
    def exact(cls, v):
        return providers.symbol(v)

    @field_validator("date")
    @classmethod
    def past(cls, v):
        if v > datetime.now(timezone.utc).date():
            raise ValueError("Trade date cannot be in the future")
        return v


class TradeCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quantity: Decimal = Field(gt=0, allow_inf_nan=False)
    price: Decimal = Field(ge=0, allow_inf_nan=False)
    date: date
    notes: str = Field(default="", max_length=1000)

    @field_validator("date")
    @classmethod
    def past(cls, v):
        return Trade.past(v)


def trade_input(t):
    return Trade.model_validate(
        {k: v for k, v in t.items() if k in Trade.model_fields}
    ).model_dump(mode="json")


async def load_portfolio(repo, org, user, pid="default"):
    return await repo.load(org, user, pid)


async def list_portfolios(repo, org, user):
    return [
        {
            "id": d["id"],
            "name": d["portfolio"]["name"],
            "currency": d["portfolio"]["currency"],
        }
        for d in await repo.list(org, user)
    ]


async def save_portfolio(repo, org, user, portfolio, pid="default", expected=None):
    async def change(old, count):
        if old:
            if (
                portfolio.positions
                and portfolio.model_dump(mode="json")["positions"]
                != old["portfolio"]["positions"]
            ):
                raise ValueError("Use trade records to change holdings")
            if portfolio.currency != old["portfolio"]["currency"]:
                raise ValueError("Portfolio currency cannot be changed")
            return {
                **old,
                "portfolio": {
                    **portfolio.model_dump(mode="json"),
                    "positions": old["portfolio"]["positions"],
                },
                "saved_at": now(),
            }
        if count >= 50:
            raise ValueError("Use at most 50 portfolios per workspace")
        if portfolio.positions:
            raise ValueError("Create an empty portfolio and record BUY transactions")
        return {
            "id": pid,
            "portfolio": portfolio.model_dump(mode="json"),
            "transactions": [],
            "opening_positions": [],
            "snapshots": [],
            "saved_at": now(),
            "legacy_opening_unknown": False,
        }

    return await repo.mutate(org, user, pid, change, expected)


async def create_portfolio(repo, org, user, portfolio):
    return await save_portfolio(repo, org, user, portfolio, uuid4().hex)


async def ledger(org, doc, trades):
    if "opening_positions" not in doc:
        # Legacy transaction accounting is recovered and checked by Rust; dates remain unknown.
        recovered = await calculate(
            org,
            "recover_opening_positions",
            {
                "positions": [
                    Position.model_validate(p).model_dump(mode="json")
                    for p in doc["portfolio"]["positions"]
                ],
                "transactions": [
                    {
                        **trade_input(t),
                        "realized_gain": str(t.get("realized_gain", 0)),
                        "total_value": str(t.get("total_value", 0)),
                    }
                    for t in doc.get("transactions", [])
                ],
            },
        )
        opening = recovered["positions"]
    else:
        opening = doc["opening_positions"]
    result = await calculate(
        org,
        "portfolio_ledger",
        {
            "opening_positions": opening,
            "transactions": [trade_input(t) for t in trades],
        },
    )
    return {
        **doc,
        "opening_positions": opening,
        "portfolio": {**doc["portfolio"], "positions": result["positions"]},
        "transactions": result["transactions"],
        "realized_gain": result["realized_gain"],
        "dividends": result["dividends"],
        "saved_at": now(),
    }


async def record_trade(repo, org, user, pid, trade, expected=None):
    async def change(doc, count):
        if doc is None:
            raise ValueError("Portfolio not found")
        trades = doc.get("transactions", [])
        item = trade.model_dump(mode="json")
        for previous in trades:
            if previous["id"] == trade.id:
                if trade_input(previous) != item:
                    raise ValueError("Trade ID already used with different details")
                return doc
        if len(trades) >= 2000:
            raise ValueError("Portfolio transaction limit reached")
        if trades and item["date"] < trades[-1]["date"]:
            raise ValueError("Record trades in chronological order")
        return await ledger(org, doc, [*trades, item])

    return await repo.mutate(org, user, pid, change, expected)


async def correct_trade(repo, org, user, pid, transaction_id, correction, expected):
    async def change(doc, count):
        if doc is None:
            raise ValueError("Portfolio not found")
        trades = doc.get("transactions", [])
        previous = next((t for t in trades if t["id"] == transaction_id), None)
        if previous is None:
            raise ValueError("Transaction not found")
        original = trade_input(previous)
        replacement = Trade.model_validate(
            {**original, **correction.model_dump(mode="json")}
        ).model_dump(mode="json")
        if original == replacement:
            return doc
        audit = doc.get("transaction_corrections", [])
        if len(audit) >= 2000:
            raise ValueError("Portfolio correction limit reached")
        changed = [
            replacement if t["id"] == transaction_id else trade_input(t) for t in trades
        ]
        changed.sort(
            key=lambda t: t["date"]
        )  # Stable order for transactions on the same date.
        result = await ledger(
            org, doc, changed
        )  # Oversells or invalid histories fail before commit.
        today = datetime.now(timezone.utc).date().isoformat()
        return {
            **result,
            "performance_reset_date": today,
            "transaction_corrections": [
                *audit,
                {
                    "transaction_id": transaction_id,
                    "previous": original,
                    "replacement": replacement,
                    "corrected_at": now(),
                    "previous_revision": doc.get("revision", 0),
                    "previous_daily_snapshot": next(
                        (p for p in doc.get("snapshots", []) if p["date"] == today),
                        None,
                    ),
                },
            ],
        }

    return await repo.mutate(org, user, pid, change, expected)


async def delete_portfolio(repo, org, user, pid, expected=None):
    async def change(doc, count):
        if doc is None:
            raise ValueError("Portfolio not found")
        return None

    await repo.mutate(org, user, pid, change, expected)


async def snapshot(org, portfolio):
    fetched = await asyncio.gather(
        *(asyncio.to_thread(providers.quote, p.symbol) for p in portfolio.positions),
        return_exceptions=True,
    )
    errors = []
    quotes = []
    for p, q in zip(portfolio.positions, fetched):
        if isinstance(q, Exception):
            errors.append({"symbol": p.symbol, "error": sanitize_error(q)})
        elif q["currency"] != portfolio.currency:
            errors.append(
                {
                    "symbol": p.symbol,
                    "error": "Quote currency differs; FX conversion is not available",
                }
            )
        else:
            quotes.append(q)
    result = {
        "positions": [],
        "errors": errors,
        "market_value": None,
        "cost_basis": None,
        "unrealized_gain": None,
        "currency": portfolio.currency,
        "fetched_at": now(),
        "comparison": None,
        "comparison_error": None,
    }
    if errors:
        return result
    native = await calculate(
        org,
        "portfolio_summary",
        {
            "currency": portfolio.currency,
            "sectors": {q["symbol"]: q.get("sector", "Unclassified") for q in quotes},
            "positions": [p.model_dump(mode="json") for p in portfolio.positions],
            "quotes": [
                {k: q[k] for k in ("symbol", "currency", "price")} for q in quotes
            ],
        },
    )
    by_symbol = {q["symbol"]: q for q in quotes}
    return {
        **result,
        **native,
        "positions": [{**p, **by_symbol[p["symbol"]]} for p in native["positions"]],
    }


async def saved_summary(repo, org, user, pid):
    doc = await repo.load(org, user, pid)
    if doc is None:
        raise ValueError("Portfolio not found")
    portfolio = Portfolio.model_validate(doc["portfolio"])
    result = await snapshot(org, portfolio)
    if result["market_value"] is not None and (
        portfolio.positions or doc.get("transactions")
    ):

        async def change(current, count):
            if current is None:
                raise ValueError("Portfolio not found")
            day = datetime.now(timezone.utc).date().isoformat()
            point = {
                "date": day,
                "market_value": result["market_value"],
                "cost_basis": result["cost_basis"],
                "unrealized_gain": result["unrealized_gain"],
                "fetched_at": result["fetched_at"],
            }
            return {
                **current,
                "snapshots": [
                    *[p for p in current.get("snapshots", []) if p["date"] != day],
                    point,
                ][-366:],
            }

        doc = await repo.mutate(org, user, pid, change, doc.get("revision", 0))
    result.update(
        history=doc.get("snapshots", []),
        transactions=doc.get("transactions", []),
        realized_gain=doc.get("realized_gain"),
        dividends=doc.get("dividends"),
        revision=doc.get("revision", 0),
        legacy_opening_unknown=doc.get("legacy_opening_unknown", False),
        performance_reset_date=doc.get("performance_reset_date"),
    )
    try:
        benchmark = await asyncio.to_thread(providers.quote, portfolio.benchmark)
        if benchmark["currency"] != portfolio.currency:
            raise ValueError(
                "Benchmark currency differs; FX conversion is not available"
            )
        quotes = {p["symbol"]: p["currency"] for p in result["positions"]}
        quotes[portfolio.benchmark] = benchmark["currency"]
        if result["errors"]:
            raise ValueError("Comparison unavailable while any quote is missing")
        series = await asyncio.to_thread(
            providers.price_series, list(quotes), quotes, portfolio.period
        )
        common = {
            "currency": portfolio.currency,
            "benchmark": portfolio.benchmark,
            "positions": [p.model_dump(mode="json") for p in portfolio.positions],
            "series": series,
            "transactions": [trade_input(t) for t in doc.get("transactions", [])],
        }
        recorded = [
            p
            for p in doc.get("snapshots", [])
            if p["date"] >= doc.get("performance_reset_date", "")
        ]
        if len(recorded) >= 2:
            result["comparison"] = await calculate(
                org,
                "portfolio_performance",
                {
                    **common,
                    "method": "recorded",
                    "snapshots": [
                        {"date": p["date"], "market_value": str(p["market_value"])}
                        for p in recorded
                    ],
                },
            )
        elif portfolio.positions:
            result["projection"] = await calculate(
                org, "portfolio_performance", {**common, "method": "projection"}
            )
            result["comparison_error"] = (
                "Recorded performance needs at least two common valuation dates. Projection is shown separately."
            )
        else:
            result["comparison_error"] = (
                "Record a BUY transaction to start tracking performance"
            )
    except Exception as error:
        result["comparison_error"] = sanitize_error(error)
    return result


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["New", "Merge"] = "New"
    data: dict
    preview_hash: str | None = None
    expected_revision: int | None = None


def import_trades(data):
    if len(json.dumps(data, allow_nan=False)) > 1048576:
        raise ValueError("Import exceeds 1 MiB")
    if (
        not isinstance(data.get("portfolio_name"), str)
        or not data["portfolio_name"].strip()
        or not isinstance(data.get("transactions"), list)
        or not data["transactions"]
    ):
        raise ValueError(
            "Expected Financial transaction JSON with portfolio_name and transactions; holdings-only imports are unsupported"
        )
    if len(data["transactions"]) > 2000:
        raise ValueError("Portfolio transaction limit reached")
    seen = {}
    items = []
    for t in data["transactions"]:
        if not isinstance(t, dict):
            raise ValueError("Invalid transaction")
        normalized = {
            "symbol": t.get("symbol"),
            "side": t.get("type"),
            "quantity": t.get("quantity"),
            "price": t.get("price"),
            "date": str(t.get("date", "")).split("T")[0],
            "notes": t.get("notes", ""),
        }
        key = json.dumps(normalized, sort_keys=True)
        occurrence = seen.get(key, 0)
        seen[key] = occurrence + 1
        tid = (
            t.get("id")
            or "import_"
            + hashlib.sha256((key + str(occurrence)).encode()).hexdigest()[:48]
        )
        items.append(
            Trade.model_validate({"id": tid, **normalized}).model_dump(mode="json")
        )
    return sorted(items, key=lambda t: t["date"])


async def import_preview(repo, org, user, pid, request):
    data = request.data
    trades = import_trades(data)
    if request.mode == "New":
        portfolio = Portfolio(
            name=data["portfolio_name"],
            owner=data.get("owner", ""),
            currency=data.get("currency", "USD"),
            benchmark=data.get(
                "benchmark", "^KLSE" if data.get("currency") == "MYR" else "SPY"
            ),
        )
        doc = {
            "id": "preview",
            "portfolio": portfolio.model_dump(mode="json"),
            "opening_positions": [],
            "transactions": [],
            "snapshots": [],
            "legacy_opening_unknown": False,
            "revision": 0,
        }
    else:
        doc = await repo.load(org, user, pid)
        if doc is None:
            raise ValueError("Merge target not found")
        if doc["portfolio"]["currency"] != data.get("currency", "USD"):
            raise ValueError("Import currency differs from target")
    by_id = {t["id"]: t for t in doc.get("transactions", [])}
    for t in trades:
        if t["id"] in by_id and trade_input(by_id[t["id"]]) != t:
            raise ValueError("Trade ID conflict")
        by_id.setdefault(t["id"], t)
    merged = sorted(by_id.values(), key=lambda t: t["date"])
    for symbol in dict.fromkeys(t["symbol"] for t in trades):
        q = await asyncio.to_thread(providers.quote, symbol)
        if q["currency"] != doc["portfolio"]["currency"]:
            raise ValueError(
                "Import quote currency differs; FX conversion is not available"
            )
    result = await ledger(org, doc, merged)
    fingerprint = hashlib.sha256(
        json.dumps(
            [request.mode, data, doc.get("revision", 0)],
            sort_keys=True,
            allow_nan=False,
        ).encode()
    ).hexdigest()
    return {
        "preview_hash": fingerprint,
        "expected_revision": doc.get("revision", 0),
        "document": result,
        "transaction_count": len(result["transactions"]),
    }


async def import_commit(repo, org, user, pid, request):
    preview = await import_preview(repo, org, user, pid, request)
    if request.preview_hash != preview["preview_hash"]:
        raise ValueError("Import changed; preview again before committing")
    target = uuid4().hex if request.mode == "New" else pid

    async def change(old, count):
        if request.mode == "New" and count >= 50:
            raise ValueError("Use at most 50 portfolios per workspace")
        return {**preview["document"], "id": target}

    expected = preview["expected_revision"] if request.mode == "Merge" else None
    if request.mode == "Merge" and request.expected_revision != expected:
        raise ValueError("Portfolio changed; preview again")
    return await repo.mutate(org, user, target, change, expected)


async def export_json(repo, org, user, pid):
    doc = await repo.load(org, user, pid)
    if doc is None:
        raise ValueError("Portfolio not found")
    if doc.get("legacy_opening_unknown") and doc.get(
        "opening_positions", doc["portfolio"]["positions"]
    ):
        raise ValueError(
            "Legacy opening holdings have unknown purchase dates; reconcile them before transaction-only export"
        )
    p = doc["portfolio"]
    return {
        "format_version": "1.0",
        "portfolio_name": p["name"],
        "owner": p.get("owner", ""),
        "currency": p["currency"],
        "benchmark": p["benchmark"],
        "export_date": now(),
        "transactions": [
            {
                "id": t["id"],
                "date": t["date"],
                "symbol": t["symbol"],
                "type": t["side"],
                "quantity": t["quantity"],
                "price": t["price"],
                "total_value": t.get("total_value"),
                "notes": t.get("notes", ""),
            }
            for t in doc.get("transactions", [])
        ],
    }
