"""Validated portfolio inputs shared by routes, services, and tools."""

from datetime import date, datetime, UTC
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.infrastructure import providers


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
        if v > datetime.now(UTC).date():
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


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["New", "Merge"] = "New"
    data: dict
    preview_hash: str | None = None
    expected_revision: int | None = None
