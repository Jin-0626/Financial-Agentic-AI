"""Reusable report contract independent of backend startup."""
from decimal import Decimal
from typing import Optional, List, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResearchSource(BaseModel):
    """A retrieved tool result or explicitly supplied document/fact."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, description="Unique source identifier referenced by findings and metrics")
    tool: str = Field(min_length=1, description="Actual retrieval tool, or user_upload/user_supplied")
    reference: str = Field(min_length=1, description="Actual URL, document name, or provider result field; never invent a URL")
    as_of: Optional[str] = Field(default=None, description="Source date or timestamp; null if unknown")
    timestamp: Optional[int] = Field(default=None, strict=True, description="Original Unix provider timestamp, when supplied; null if unknown")


class ResearchMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(min_length=1)
    value: Optional[float] = Field(strict=True, description="Retrieved or evidence-derived finite number; null when unavailable")
    unit: Optional[str] = Field(default=None, description="Currency, percent, ratio, or other actual unit")
    period: Optional[str] = Field(default=None, description="Source reporting period; null if unknown")
    source_ids: List[str] = Field(description="Evidence supporting the value and any derivation")
    calculation: Optional[str] = Field(default=None, description="Explain formula, input assumptions and actual executed workpaper path for derived values; null for direct observations")

    @model_validator(mode="after")
    def require_evidence(self):
        if self.value is not None and not self.source_ids:
            raise ValueError("A numeric metric requires source evidence")
        return self


class ResearchFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field(min_length=1, description="For example business, valuation, risk, catalyst, or news")
    statement: str = Field(min_length=1)
    source_ids: List[str] = Field(min_length=1, description="Evidence for this finding; unsupported claims belong in data_gaps")


class ResearchToolError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str = Field(min_length=1)
    category: Literal["provider", "dependency", "sandbox", "validation"]
    error: str = Field(min_length=1, description="Actual sanitized error; never include credentials")


class AnalysisReport(BaseModel):
    """Validated financial research with readable Markdown and explicit evidence gaps."""
    model_config = ConfigDict(extra="forbid")
    status: Literal["success", "partial_success", "unavailable"]
    executive_summary: str = Field(min_length=1, description="Concise thesis or evidence-limited verdict, supported horizon and catalysts; cite sources, avoid duplicate headings or a metric dump")
    confidence: Literal["high", "medium", "low"]
    risks: List[ResearchFinding]
    recommendations: List[ResearchFinding]
    subject: Optional[str] = Field(default=None, description="Company, security, portfolio, or topic; null for ordinary conversation")
    metrics: List[ResearchMetric]
    key_findings: List[ResearchFinding]
    sources: List[ResearchSource]
    data_gaps: List[str] = Field(description="Missing or unverified evidence, including successful empty retrievals")
    tool_errors: List[ResearchToolError]

    @model_validator(mode="after")
    def validate_source_references(self):
        ids = [source.id for source in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("Source identifiers must be unique")
        known = set(ids)
        source_map = {source.id: source for source in self.sources}
        for item in [*self.metrics, *self.key_findings, *self.risks, *self.recommendations]:
            if not set(item.source_ids).issubset(known):
                raise ValueError("Every evidence reference must identify a provided source")
        for metric in self.metrics:
            if metric.value is not None and metric.calculation and not any(source_map[sid].tool == "execute" for sid in metric.source_ids):
                raise ValueError("Derived numeric metrics require executed-code source evidence")
        if self.status == "success" and (self.data_gaps or self.tool_errors):
            raise ValueError("Retrieval failures or gaps require partial_success or unavailable")
        if self.status == "unavailable" and (self.key_findings or self.risks or self.recommendations or any(metric.value is not None for metric in self.metrics)):
            raise ValueError("Unavailable research cannot contain purported verified findings or numeric values")
        return self


    @property
    def answer(self) -> str:
        """Compatibility text derives exclusively from validated report fields."""
        return render_report(self)


INFORMATIONAL_CAVEAT = (
    "This analysis is for educational and informational purposes only and is not "
    "individualized investment advice. Data may be delayed or incomplete; verify sources "
    "and assumptions independently. Hypothetical scenarios are not forecasts or guarantees."
)


def _format_number(value: float | None) -> str:
    if value is None:
        return "Unavailable"
    if value == 0:
        return "0"
    text = format(Decimal(str(value)), ",f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _table_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def render_report(report: AnalysisReport) -> str:
    """Render validated fields as readable sections without rounding supplied values."""
    blocks = []
    if report.subject:
        blocks.append(f"## {report.subject}")
    coverage = {"success": "Available", "partial_success": "Partial", "unavailable": "Unavailable"}[report.status]
    blocks.append(f"**Data coverage:** {coverage} | **Confidence:** {report.confidence.capitalize()}")
    blocks.append("### Executive Summary\n\n" + report.executive_summary.strip())
    sources = {source.id: source for source in report.sources}
    if report.metrics:
        rows = ["| Metric | Value | Unit | Period |", "| --- | ---: | --- | --- | --- |"]
        for metric in report.metrics:
            dates = list(dict.fromkeys(sources[sid].as_of for sid in metric.source_ids if sources[sid].as_of))
            period = metric.period or "; ".join(dates) or "Not provided"
            cells = (metric.name, _format_number(metric.value), metric.unit or "Not provided", period,
                     ", ".join(f"[{sid}]" for sid in metric.source_ids) or "Unavailable")
            rows.append("| " + " | ".join(_table_cell(cell) for cell in cells) + " |")
        blocks.append("### Financial Metrics\n\n" + "\n".join(rows))
        calculations = [f"- **{metric.name}:** {metric.calculation}" for metric in report.metrics if metric.calculation]
        if calculations:
            blocks.append("### Calculation Notes\n\n" + "\n".join(calculations))

    financial_categories = {"financial_performance", "accounting_quality", "liquidity", "balance_sheet", "profitability", "earnings_quality"}
    valuation_categories = {"valuation", "scenario", "sensitivity"}
    financial, valuation, other = [], [], []
    for finding in report.key_findings:
        category = finding.category.lower().replace("-", "_").replace(" ", "_")
        target = financial if category in financial_categories else valuation if category in valuation_categories else other
        target.append(finding)

    def findings_section(title, findings, empty_text=None):
        if findings:
            text = "\n".join(f"- {item.statement} [{', '.join(item.source_ids)}]" for item in findings)
        elif empty_text:
            text = empty_text
        else:
            return
        blocks.append(f"### {title}\n\n{text}")

    findings_section("Financial Performance and Accounting Quality", financial)
    findings_section("Valuation and Scenario Analysis", valuation)
    findings_section("Key Findings", other, "No additional verified findings available.")
    findings_section("Risks and Counter-Thesis", report.risks, "Risks could not be established from the supplied evidence; this does not imply low risk.")
    findings_section("Recommendations", report.recommendations, "No evidence-backed recommendation established.")
    blocks.append(f"### Confidence\n\n{report.confidence.capitalize()}. Confidence describes evidence coverage, not investment certainty.")
    if report.data_gaps or report.tool_errors:
        lines = [f"- {gap}" for gap in report.data_gaps]
        lines.extend(f"- **{error.tool} ({error.category}):** {error.error}" for error in report.tool_errors)
        blocks.append("### Data Gaps and Retrieval Failures\n\n" + "\n".join(lines))
    if report.sources:
        lines = []
        definitions = []
        for source in report.sources:
            reference = source.reference.strip()
            if reference.startswith(("{", "[")):
                reference = "Retrieved tool result; full reference retained in structured data"
            is_url = reference.startswith(("https://", "http://")) and not any(char.isspace() for char in reference)
            label = f"[{source.id}]({reference})" if is_url else f"[{source.id}]"
            detail = "" if is_url else f" — {reference}"
            lines.append(f"- **{label}** — {source.tool}{detail}; as of {source.as_of or 'not provided'}")
            if is_url:
                definitions.append(f"[{source.id}]: {reference}")
        blocks.append("### Sources\n\n" + "\n".join(lines))
        if definitions:
            blocks.append("\n".join(definitions))
    blocks.append("### Informational Caveat\n\n" + INFORMATIONAL_CAVEAT)
    return "\n\n".join(blocks)


FinancialResearchOutput = AnalysisReport
