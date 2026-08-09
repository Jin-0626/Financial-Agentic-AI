import math
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


WorkflowStatus = Literal["SUCCESS", "FAILED", "FALLBACK_SUCCESS"]
TelemetryStatus = Literal["success", "failed", "fallback"]


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    PROJECT_NAME: str = "KLSE Multi-Agent Research System"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    PRIMARY_MODEL: str = "gpt-oss:120b-cloud"
    FAST_MODEL: str = "minimax-m3:cloud"
    OLLAMA_API_KEY: str = Field(default="", repr=False)
    MAX_DEBATE_ROUNDS: int = Field(default=1, ge=1)
    TAVILY_API_KEY: str = Field(default="", repr=False)
    LANGSMITH_TRACING: str = "true"
    LANGSMITH_TRACING_V2: str = "true"
    LANGSMITH_API_KEY: str = Field(default="", repr=False)
    LANGSMITH_PROJECT: str = "Financial Analyst"


class BursaTickerRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Bursa ticker, stock code, or company name.")

    @field_validator("query")
    @classmethod
    def valid_query(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Ticker or company name is required.")
        if not any(ch.isalnum() or ch in ".- " for ch in clean):
            raise ValueError("Ticker or company name must contain at least one alphanumeric character.")
        return clean


class StockInfoResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str
    company_name: str
    source: Literal["yfinance"] = "yfinance"
    status: Literal["SUCCESS", "FAILED"]
    sector: str = "N/A"
    industry: str = "N/A"
    currency: str = "MYR"
    current_price: Optional[float] = None
    pe_ratio: Optional[float] = None
    forward_pe: Optional[float] = None
    dividend_yield: Optional[float] = None
    fifty_two_week_high: Any = "N/A"
    fifty_two_week_low: Any = "N/A"
    market_cap: Any = None
    summary: str = ""
    error: Optional[str] = None
    error_type: Optional[str] = None

    @field_validator("current_price", "pe_ratio", "forward_pe")
    @classmethod
    def finite_positive_or_none(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            return None
        return number


class QuarterlyReportResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str
    company_name: str
    source: Literal["yfinance", "tavily_search", "none"]
    status: WorkflowStatus
    fallback_used: bool = False
    fallback_provider: Optional[Literal["tavily_search"]] = None
    fallback_reason: Optional[str] = None
    quarterly_financials: Optional[Dict[Any, Any]] = None
    extracted_reports: Optional[List[Dict[str, Any]]] = None
    search_query: Optional[str] = None
    error: Optional[str] = None

    @model_validator(mode="after")
    def fallback_shape_is_explicit(self):
        if self.source == "tavily_search":
            self.fallback_used = True
            self.fallback_provider = self.fallback_provider or "tavily_search"
        return self


class DCFValuationRequest(BaseModel):
    current_price: Optional[float] = Field(None, description="Authoritative market price in MYR.")
    pe_ratio: Optional[float] = Field(None, description="Trailing P/E ratio when available.")
    growth_rate: float = Field(0.08, ge=-0.5, le=1.0)
    wacc: float = Field(0.08, ge=0.0, le=1.0)
    terminal_growth_rate: float = Field(0.02, ge=-0.5, le=1.0)


class DCFValuationResult(BaseModel):
    status: Literal["SUCCESS", "FAILED"]
    estimated_fair_value_myr: Optional[float] = None
    upside_downside_pct: Optional[float] = None
    pe_ratio_used: Optional[float] = None
    growth_rate: Optional[float] = None
    wacc: Optional[float] = None
    discount_rate: Optional[float] = None
    terminal_growth_rate: Optional[float] = None
    terminal_pe: Optional[float] = None
    eps_input: Optional[float] = None
    projected_eps: List[float] = Field(default_factory=list)
    projected_fcff_per_share: List[float] = Field(default_factory=list)
    pe_ratio_substituted: bool = False
    pe_ratio_substitution_reason: Optional[str] = None
    error: Optional[str] = None


class ModelingSummaryResult(BaseModel):
    content: str = Field(..., min_length=1)
    source: Literal["llm", "deterministic_fallback"] = "llm"
    fallback_reason: Optional[str] = None


class FinancialMetricsResult(BaseModel):
    analysis_notes: str = Field(..., min_length=1)
    pe_ratio: Optional[float] = None
    div_yield: Optional[float] = None
    source: Literal["deterministic"] = "deterministic"


class ResearchPlan(BaseModel):
    ticker: str = Field(..., min_length=1)
    company_name: str = Field(..., min_length=1)
    research_objective: str = Field(..., min_length=1)
    required_agents: List[str] = Field(..., min_length=1)
    data_quality: Literal["complete", "partial", "degraded"]
    valuation_method: Literal["dcf_pe_proxy"]
    debate_required: bool = True
    risks_to_test: List[str] = Field(default_factory=list)
    data_gaps: List[str] = Field(default_factory=list)
    recovery_actions: List[str] = Field(default_factory=list)
    replanning_events: List[str] = Field(default_factory=list)

    @field_validator("required_agents")
    @classmethod
    def required_agents_are_known(cls, value: List[str]) -> List[str]:
        allowed = {
            "data_agent",
            "analysis_agent",
            "modeling_agent",
            "synthesis_agent",
            "bull_agent",
            "bear_agent",
            "debate_agent",
            "replanner_agent",
            "judge_agent",
            "report_agent",
        }
        unknown = [agent for agent in value if agent not in allowed]
        if unknown:
            raise ValueError(f"Unknown planned agents: {unknown}")
        return value


class DebateCaseResult(BaseModel):
    stance: Literal["bull", "bear"]
    content: str = Field(..., min_length=1)
    source: Literal["llm", "deterministic_fallback"] = "llm"
    fallback_reason: Optional[str] = None


class DebateBrief(BaseModel):
    bull_summary: str = Field(..., min_length=1)
    bear_summary: str = Field(..., min_length=1)
    contested_points: List[str] = Field(default_factory=list)
    decision_questions: List[str] = Field(default_factory=list)
    source: Literal["deterministic"] = "deterministic"


class ReplanDecision(BaseModel):
    triggered: bool
    data_quality: Literal["complete", "partial", "degraded"]
    recovery_actions: List[str] = Field(default_factory=list)
    replanning_events: List[str] = Field(default_factory=list)
    judge_can_continue: bool = True


class NodeMetric(BaseModel):
    node: str
    model: Optional[str] = None
    latency_ms: float = Field(..., ge=0)
    prompt_tokens: Optional[int] = Field(None, ge=0)
    completion_tokens: Optional[int] = Field(None, ge=0)
    total_tokens: Optional[int] = Field(None, ge=0)
    status: TelemetryStatus
    error_type: Optional[str] = None
    tool_calls: int = Field(0, ge=0)
    tool_errors: int = Field(0, ge=0)
    llm_calls: int = Field(0, ge=0)
    retry_count: int = Field(0, ge=0)
    downgraded: bool = False
    fallback_used: bool = False
    fallback_provider: Optional[str] = None
    fallback_reason: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def token_total_is_consistent(self):
        if self.total_tokens is None and self.prompt_tokens is not None and self.completion_tokens is not None:
            self.total_tokens = self.prompt_tokens + self.completion_tokens
        return self


class WorkflowError(BaseModel):
    node: str
    type: str
    message: str


class FailureState(BaseModel):
    workflow_status: Literal["FAILED"] = "FAILED"
    failed_stage: str
    error_message: str
    errors: List[WorkflowError] = Field(default_factory=list)
    audit_trace: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def default_error_and_trace(self):
        if not self.errors:
            self.errors = [
                WorkflowError(
                    node=self.failed_stage,
                    type="WorkflowError",
                    message=self.error_message,
                )
            ]
        if not self.audit_trace:
            self.audit_trace = [f"FAILED [{self.failed_stage}]: {self.error_message}"]
        return self


class InvestmentVerdict(BaseModel):
    raw_judgement: str = Field(..., min_length=1)
    target_price_myr: Optional[float] = None
    valid: bool = True
