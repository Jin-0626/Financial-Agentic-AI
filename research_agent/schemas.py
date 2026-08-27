import math
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DataStatus = Literal["SUCCESS", "FAILED", "FALLBACK_SUCCESS", "PARTIAL"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def finite_positive_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


class BursaTickerRequest(BaseModel):
    query: str = Field(..., min_length=1)

    @field_validator("query")
    @classmethod
    def valid_query(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Ticker or company name is required.")  # noqa: TRY003
        if not any(ch.isalnum() or ch in ".- " for ch in clean):
            raise ValueError(  # noqa: TRY003
                "Ticker or company name must contain at least one alphanumeric character."
            )
        return clean


class SourceRecord(BaseModel):
    title: str
    url: str
    provider: str ="bursa_official"
    retrieved_at: str = Field(default_factory=utc_now_iso)
    confidence: Literal["high", "medium", "low"] = "medium"
    published_date: str | None = None
    snippet: str = ""


class StockInfoResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str
    company_name: str
    source: Literal["yfinance"]
    status: Literal["SUCCESS", "FAILED"]
    retrieved_at: str = Field(default_factory=utc_now_iso)
    confidence: Literal["high", "medium", "low"] = "medium"
    warnings: list[str] = Field(default_factory=list)
    sector: str = "N/A"
    industry: str = "N/A"
    currency: str = "MYR"
    current_price: float | None = None
    pe_ratio: float | None = None
    forward_pe: float | None = None
    dividend_yield: float | None = None
    fifty_two_week_high: Any = "N/A"
    fifty_two_week_low: Any = "N/A"
    market_cap: Any = None
    price_to_book: float | None = None
    price_to_sales: float | None = None
    enterprise_value: Any = None
    enterprise_to_ebitda: float | None = None
    trailing_eps: float | None = None
    book_value: float | None = None
    summary: str = ""
    error: str | None = None

    @field_validator(
        "current_price",
        "pe_ratio",
        "forward_pe",
        "price_to_book",
        "price_to_sales",
        "enterprise_to_ebitda",
        "trailing_eps",
        "book_value",
    )
    @classmethod
    def finite_positive_or_none(cls, value: float | None) -> float | None:
        return finite_positive_number(value)


class QuarterlyReportResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str
    company_name: str
    source: Literal["yfinance", "official_search", "tavily_search", "none"]
    status: DataStatus
    retrieved_at: str = Field(default_factory=utc_now_iso)
    confidence: Literal["high", "medium", "low"] = "medium"
    warnings: list[str] = Field(default_factory=list)
    quarterly_financials: dict[Any, Any] | None = None
    sources: list[SourceRecord] = Field(default_factory=list)
    fallback_used: bool = False
    fallback_provider: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def fallback_shape_is_explicit(self):
        if self.source in {"official_search", "tavily_search"} and self.source != "yfinance":
            self.fallback_used = self.source == "tavily_search"
            self.fallback_provider = self.source if self.fallback_used else self.fallback_provider
        return self


class OfficialResearchResult(BaseModel):
    query: str
    status: DataStatus
    retrieved_at: str = Field(default_factory=utc_now_iso)
    confidence: Literal["high", "medium", "low"] = "medium"
    warnings: list[str] = Field(default_factory=list)
    sources: list[SourceRecord] = Field(default_factory=list)


class DCFValuationRequest(BaseModel):
    current_price: float | None = Field(None, description="Authoritative market price in MYR.")
    pe_ratio: float | None = Field(None, description="Trailing P/E ratio when available.")
    growth_rate: float = Field(0.08, ge=-0.5, le=1.0)
    wacc: float = Field(0.08, ge=0.0, le=1.0)
    terminal_growth_rate: float = Field(0.02, ge=-0.5, le=1.0)


class DCFValuationResult(BaseModel):
    status: Literal["SUCCESS", "FAILED"]
    retrieved_at: str = Field(default_factory=utc_now_iso)
    confidence: Literal["high", "medium", "low"] = "medium"
    warnings: list[str] = Field(default_factory=list)
    estimated_fair_value_myr: float | None = None
    upside_downside_pct: float | None = None
    pe_ratio_used: float | None = None
    growth_rate: float | None = None
    wacc: float | None = None
    terminal_growth_rate: float | None = None
    terminal_pe: float | None = None
    eps_input: float | None = None
    projected_eps: list[float] = Field(default_factory=list)
    projected_fcff_per_share: list[float] = Field(default_factory=list)
    pe_ratio_substituted: bool = False
    pe_ratio_substitution_reason: str | None = None
    error: str | None = None


class ReportFormatResult(BaseModel):
    status: Literal["SUCCESS"]
    retrieved_at: str = Field(default_factory=utc_now_iso)
    report_markdown: str
    disclaimer: str

class StockAnalysisReport(BaseModel):
    ticker: str
    current_price: float
    pe_ratio: float = Field(description="Trailing P/E ratio")
    recommendation: str = Field(description="BUY, HOLD, or SELL with justification")
    buy_price_range: str = Field(description="Suggested entry price range (e.g., RM 0.48 - RM 0.51)")
    sell_price_range: str = Field(description="Suggested target exit range (e.g., RM 0.58 - RM 0.64)")
    key_risks: list[str]
    
class TradeLevelsResult(BaseModel):
    symbol: str
    current_price: float
    support_level: float
    resistance_level: float
    buy_range_min: float
    buy_range_max: float
    sell_range_min: float
    sell_range_max: float
    rsi: float
    rsi_signal: str
    ema_trend: str
    atr: float