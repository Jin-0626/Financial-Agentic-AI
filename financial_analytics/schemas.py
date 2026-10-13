"""Operation-specific inputs shared by dispatch and isolated workers."""
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

Number = FiniteFloat
Money = Annotated[Decimal, Field(allow_inf_nan=False, max_digits=28, decimal_places=10)]
Positive = Annotated[Number, Field(gt=0)]
Series = Annotated[list[Number], Field(min_length=2, max_length=10000)]
Prices = Annotated[list[Positive], Field(min_length=2, max_length=10000)]


class Inputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def reject_booleans(cls, data):
        def visit(value):
            if isinstance(value, bool):
                raise ValueError("Boolean values are not numeric inputs")
            if isinstance(value, dict):
                for entry in value.values(): visit(entry)
            elif isinstance(value, (list, tuple)):
                for entry in value: visit(entry)
        visit(data)
        return data


class MonetaryInputs(Inputs):
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None


class CashFlows(MonetaryInputs):
    cash_flows: Annotated[list[Money], Field(min_length=1, max_length=10000)]
    discount_rate: Annotated[Number, Field(gt=-1, le=10)] = 0.1


class Growth(Inputs):
    initial: Positive
    final: Positive
    years: Annotated[Number, Field(gt=0, le=1000)]


class Dividend(MonetaryInputs):
    dividend: Annotated[Money, Field(ge=0)]
    discount_rate: Annotated[Number, Field(gt=0, le=10)]
    growth_rate: Annotated[Number, Field(gt=-1, le=10)]
    @model_validator(mode="after")
    def sustainable(self):
        if self.growth_rate >= self.discount_rate: raise ValueError("Growth must be below discount rate")
        return self


class Capital(MonetaryInputs):
    equity: Annotated[Money, Field(ge=0)]
    debt: Annotated[Money, Field(ge=0)]
    cost_equity: Annotated[Number, Field(ge=0, le=10)]
    cost_debt: Annotated[Number, Field(ge=0, le=10)]
    tax_rate: Annotated[Number, Field(ge=0, le=1)] = 0
    @model_validator(mode="after")
    def funded(self):
        if self.equity + self.debt <= 0: raise ValueError("Capital must be positive")
        return self


class Statements(MonetaryInputs):
    revenue: Money
    net_income: Money
    operating_income: Money
    assets: Annotated[Money, Field(gt=0)]
    equity: Money
    liabilities: Money
    current_assets: Money
    current_liabilities: Money
    operating_cash_flow: Money
    capital_expenditure: Annotated[Money, Field(ge=0)]


class Bond(MonetaryInputs):
    face: Annotated[Money, Field(gt=0)] = Decimal("1000")
    coupon_rate: Annotated[Number, Field(ge=0, le=10)]
    yield_rate: Annotated[Number, Field(gt=-1, le=10)]
    years: Annotated[int, Field(strict=True, ge=1, le=100)]
    frequency: Literal[1, 2, 4, 12] = 2


class Option(MonetaryInputs):
    spot: Annotated[Money, Field(gt=0)]
    strike: Annotated[Money, Field(gt=0)]
    years: Annotated[Number, Field(ge=0, le=30)]
    rate: Annotated[Number, Field(ge=-1, le=1)] = 0.03
    dividend_yield: Annotated[Number, Field(ge=0, le=1)] = 0
    volatility: Annotated[Number, Field(gt=0, le=5)] = 0.2
    side: Literal["call", "put"] = "call"
    steps: Annotated[int, Field(strict=True, ge=1, le=2000)] = 100


class Forward(MonetaryInputs):
    spot: Annotated[Money, Field(gt=0)]
    years: Annotated[Number, Field(ge=0, le=30)]
    rate: Annotated[Number, Field(ge=-1, le=1)]
    income_yield: Annotated[Number, Field(ge=-1, le=1)] = 0


class Samples(Inputs):
    values: Series
    comparison: Series | None = None
    confidence: Annotated[Number, Field(gt=0, lt=1)] = 0.95


class Returns(Inputs):
    returns: Annotated[list[Annotated[Number, Field(ge=-1)]], Field(min_length=2, max_length=10000)]
    benchmark: Series | None = None
    periods_per_year: Annotated[int, Field(strict=True, ge=1, le=365)] = 252
    annual_risk_free_rate: Annotated[Number, Field(gt=-1, le=1)] = 0
    confidence: Annotated[Number, Field(gt=0, lt=1)] = 0.95
    @model_validator(mode="after")
    def aligned(self):
        if self.benchmark is not None and len(self.benchmark) != len(self.returns): raise ValueError("Benchmark must align with returns")
        return self


class Technical(Inputs):
    prices: Prices
    window: Annotated[int, Field(strict=True, ge=2, le=2520)] = 14
    @model_validator(mode="after")
    def sufficient(self):
        if self.window >= len(self.prices): raise ValueError("More prices than the window are required")
        return self


class Forecast(Inputs):
    values: Annotated[list[Number], Field(min_length=10, max_length=10000)]
    horizon: Annotated[int, Field(strict=True, ge=1, le=252)] = 10
    order: tuple[Annotated[int, Field(ge=0, le=5)], Annotated[int, Field(ge=0, le=2)], Annotated[int, Field(ge=0, le=5)]] = (1, 0, 0)


class Matrix(Inputs):
    values: Annotated[list[Annotated[list[Number], Field(min_length=1, max_length=50)]], Field(min_length=10, max_length=10000)]
    @model_validator(mode="after")
    def rectangular(self):
        if len({len(row) for row in self.values}) != 1: raise ValueError("Rows must have equal length")
        return self


class Optimization(Matrix):
    method: Literal["minimum_variance", "maximum_sharpe", "risk_parity"] = "minimum_variance"
    max_weight: Annotated[Number, Field(gt=0, le=1)] = 1
    periods_per_year: Annotated[int, Field(strict=True, ge=1, le=365)] = 252
    annual_risk_free_rate: Annotated[Number, Field(ge=-1, le=1)] = 0
    @model_validator(mode="after")
    def feasible(self):
        if len(self.values[0]) < 2 or self.max_weight * len(self.values[0]) < 1: raise ValueError("Infeasible asset count or weight cap")
        if any(x < -1 for row in self.values for x in row): raise ValueError("Simple returns cannot be below -1")
        return self


class Regression(Matrix):
    target: Series
    @model_validator(mode="after")
    def paired(self):
        if len(self.values) != len(self.target): raise ValueError("Targets must align with rows")
        if len(self.values) <= len(self.values[0]) + 2: raise ValueError("Insufficient degrees of freedom")
        return self


class Learning(Regression):
    seed: Annotated[int, Field(strict=True, ge=0, le=2147483647)] = 42
    test_fraction: Annotated[Number, Field(gt=0, le=0.5)] = 0.2


class Backtest(MonetaryInputs):
    prices: Annotated[list[Positive], Field(min_length=3, max_length=10000)]
    dates: Annotated[list[date], Field(min_length=3, max_length=10000)]
    strategy: Literal["buy_and_hold", "moving_average_crossover", "rsi"] = "buy_and_hold"
    initial_cash: Annotated[Money, Field(gt=0)] = Decimal("10000")
    cost_bps: Annotated[Number, Field(ge=0, le=1000)] = 10
    fast_window: Annotated[int, Field(strict=True, ge=2, le=2520)] = 5
    slow_window: Annotated[int, Field(strict=True, ge=3, le=2520)] = 20
    rsi_window: Annotated[int, Field(strict=True, ge=2, le=2520)] = 14
    lower: Annotated[Number, Field(ge=0, le=100)] = 30
    upper: Annotated[Number, Field(ge=0, le=100)] = 70
    @model_validator(mode="after")
    def chronological(self):
        if len(self.prices) != len(self.dates) or any(a >= b for a, b in zip(self.dates, self.dates[1:])): raise ValueError("Dates must be unique, increasing, and aligned")
        if self.fast_window >= self.slow_window or self.lower >= self.upper: raise ValueError("Invalid strategy thresholds")
        if self.strategy == "moving_average_crossover" and len(self.prices) <= self.slow_window: raise ValueError("Insufficient crossover history")
        if self.strategy == "rsi" and len(self.prices) <= self.rsi_window: raise ValueError("Insufficient RSI history")
        return self


Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")]
class NativeRisk(Inputs):
    ticker: Identifier
    lookback_days: Annotated[int, Field(strict=True, ge=2, le=10000)] = 252
    confidence_level: Annotated[Number, Field(gt=0, lt=1)] = 0.95
    snapshot_id: Identifier | None = None

class NativeSimulation(Inputs):
    ticker: Identifier
    lookback_days: Annotated[int, Field(strict=True, ge=2, le=10000)] = 252
    simulations: Annotated[int, Field(strict=True, ge=100, le=100000)] = 1000
    horizon_days: Annotated[int, Field(strict=True, ge=1, le=2520)] = 252
    seed: Annotated[int, Field(strict=True, ge=0, le=2147483647)] = 42
    snapshot_id: Identifier | None = None

class NativeDcf(Inputs):
    ticker: Identifier
    wacc: Annotated[Number, Field(gt=0, le=1)]
    terminal_growth_rate: Annotated[Number, Field(gt=-1, lt=1)]
    historical_years: Annotated[int, Field(strict=True, ge=1, le=20)] = 3
    forecast_id: Identifier
    snapshot_id: Identifier | None = None
    @model_validator(mode="after")
    def sustainable(self):
        if self.terminal_growth_rate >= self.wacc: raise ValueError("Growth must be below WACC")
        return self

class NativeRatios(Inputs):
    ticker: Identifier
    period: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    snapshot_id: Identifier | None = None


class PrivateMarkets(MonetaryInputs):
    cash_flows: Annotated[list[Money], Field(min_length=2, max_length=10000)]
    benchmark_levels: Prices
    terminal_nav: Annotated[Money, Field(ge=0)] = Decimal("0")
    @model_validator(mode="after")
    def paired(self):
        if len(self.cash_flows) != len(self.benchmark_levels): raise ValueError("Cash flows and benchmark levels must align")
        if not any(x < 0 for x in self.cash_flows): raise ValueError("PME requires invested capital")
        return self


class ImpliedVolatility(Option):
    market_price: Annotated[Money, Field(gt=0)]
    @model_validator(mode="after")
    def unexpired(self):
        if self.years<=0: raise ValueError("Implied volatility requires positive time to expiry")
        return self
