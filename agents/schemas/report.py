from typing import Literal

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    """A compact, source-linked fact passed between DeepAgents."""

    claim: str = Field(description="One factual claim extracted from a filing or news source.")
    value: str | None = Field(default=None, description="Exact numeric/text value when present.")
    period: str | None = Field(default=None, description="Reporting period or publication date.")
    period_basis: Literal["current_quarter", "cumulative_period", "point_in_time", "unknown"] = Field(
        default="unknown",
        description="Whether the value is current-quarter, cumulative interim period, point-in-time, or unclear.",
    )
    source: str = Field(description="Source label, URL, or RAG chunk reference.")
    source_type: Literal["filing", "news", "company", "market", "derived", "unknown"] = Field(
        default="unknown",
        description="Where the fact came from.",
    )
    confidence: Literal["low", "medium", "high"] = Field(
        default="medium",
        description="Confidence based on source quality and specificity.",
    )


class FundamentalFindings(BaseModel):
    """Structured output from the filing fundamentals subagent."""

    company_name: str
    stock_code: str
    reporting_period: str | None = None
    period_notes: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    summary: str = Field(description="Brief filing-backed summary without unsupported numbers.")


class BullBearAssessment(BaseModel):
    """Structured bull/bear stress test constrained to supplied evidence."""

    bull_arguments: list[str] = Field(default_factory=list)
    bear_arguments: list[str] = Field(default_factory=list)
    balanced_view: str = Field(description="Short synthesis of which side is better supported.")
    unsupported_assumptions: list[str] = Field(default_factory=list)


class ValidationReport(BaseModel):
    """Validation result for a draft report or planned final answer."""

    passed: bool = Field(description="Whether the report is safe to present as-is.")
    warnings: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    required_corrections: list[str] = Field(default_factory=list)


class ReportSectionSpec(BaseModel):
    """Pydantic-backed section contract for the final committee briefing."""

    title: str
    required_elements: list[str] = Field(default_factory=list)
    evidence_rule: str = Field(
        default=(
            "Every exact figure must be traceable in internal evidence, but the final report must not show "
            "raw source IDs, chunk IDs, RAG labels, URLs, or citation clutter."
        )
    )


class InstitutionalBriefingFormat(BaseModel):
    """Readable markdown layout inspired by the preferred LangGraph run format."""

    title_pattern: str = "{company_name} ({stock_code}) - Institutional Investment Committee Briefing"
    sections: list[ReportSectionSpec]
    verdict_labels: list[Literal["STRONG BUY", "ACCUMULATE", "HOLD", "AVOID"]] = Field(
        default_factory=lambda: ["STRONG BUY", "ACCUMULATE", "HOLD", "AVOID"]
    )
    unsupported_value_label: str = "Unavailable in supplied evidence"
    source_display_policy: str = (
        "Keep source IDs, chunk IDs, RAG labels, and URLs out of the final user-facing markdown. "
        "Use human-readable evidence basis labels such as 'Q2 2026 quarterly filing excerpt', "
        "'latest Bursa filing evidence', or 'live market-intel summary' when context is helpful."
    )

    def as_prompt(self) -> str:
        lines = [
            "Final markdown format contract:",
            f"- Title pattern: {self.title_pattern}",
            f"- Use this exact missing-data label: {self.unsupported_value_label}",
            f"- Source display policy: {self.source_display_policy}",
            "- Do not include columns named Source, Source(s), Citation, Chunk, Chunk ID, or URL in the final report.",
            "- Do not print raw hashes, RAG chunk references, JSON evidence objects, or validator correction payloads.",
        ]
        for index, section in enumerate(self.sections, start=1):
            lines.append(f"{index}. {section.title}")
            for element in section.required_elements:
                lines.append(f"   - {element}")
            lines.append(f"   - Evidence rule: {section.evidence_rule}")
        return "\n".join(lines)


def committee_briefing_format() -> InstitutionalBriefingFormat:
    """Return the preferred final-report layout without trusting any run artifact as instruction."""

    return InstitutionalBriefingFormat(
        sections=[
            ReportSectionSpec(
                title="Executive Summary & Core Identity",
                required_elements=[
                    "Company, stock code, sector/core business, latest reporting period, verdict, and one concise thesis paragraph.",
                    "Mention whether the latest filing evidence is current-quarter, cumulative-period, or unclear.",
                ],
            ),
            ReportSectionSpec(
                title="Segmental Revenue & Profitability Breakdown",
                required_elements=[
                    "Markdown table with Segment, Revenue, Profit/PBT where disclosed, Period/Basis, and Comment.",
                    "No derived gross margin or YoY/QoQ percentage unless the supplied evidence contains the numerator, denominator, and comparable period basis.",
                ],
            ),
            ReportSectionSpec(
                title="Balance Sheet & Cash Flow Health",
                required_elements=[
                    "Markdown table for assets, liabilities, borrowings/debt, lease liabilities, cash/cash equivalents, operating cash flow, interest expense, and dividends.",
                    "Each unavailable metric must remain unavailable instead of being estimated from snippets or general knowledge.",
                    "Do not add source columns; briefly mention the filing period in the metric/comment text when needed.",
                ],
            ),
            ReportSectionSpec(
                title="Bull vs. Bear Debate Matrix",
                required_elements=[
                    "Markdown table with Issue/Theme, Bull Position, Bear Rebuttal, and Evidence Limitation.",
                    "Unsupported assumptions must be called out inside the matrix rather than converted into facts.",
                ],
            ),
            ReportSectionSpec(
                title="Committee Verdict & Action Plan",
                required_elements=[
                    "Markdown verdict table with Rating, Rationale, Target Range, and Monitoring Triggers.",
                    "Use 'Target range unavailable due to insufficient quantitative evidence' unless a source-supported valuation basis exists.",
                ],
            ),
        ]
    )


class AgentAnalysisOutput(BaseModel):
    """Structured analytical synthesis generated by the Portfolio Lead."""

    verdict: Literal["STRONG BUY", "ACCUMULATE", "HOLD", "AVOID"] = Field(
        description="Final investment recommendation based on fundamentals and liquidity."
    )
    intrinsic_low: float | None = Field(
        default=None,
        description="Lower bound of intrinsic valuation estimate in MYR."
    )
    intrinsic_high: float | None = Field(
        default=None,
        description="Upper bound of intrinsic valuation estimate in MYR."
    )
    valuation_basis: str = Field(
        default="No valuation basis provided.",
        description="Evidence-backed basis for the valuation range, or why it is unavailable.",
    )
    valuation_confidence: Literal["low", "medium", "high"] = Field(
        default="low",
        description="Confidence in the valuation range.",
    )
    fundamental_findings: list[str] = Field(
        description="Key balance sheet, MFRS 16, or cash flow points extracted from filings."
    )
    bull_arguments: list[str] = Field(
        description="Core catalysts, store network rollouts, and margin expansion drivers."
    )
    bear_arguments: list[str] = Field(
        description="Risks such as minimum wage increases, FX pressure, and liquidity traps."
    )
    risk_mitigations: list[str] = Field(
        description="Execution guidelines, such as max ADV daily order limits."
    )
    executive_summary: str = Field(
        description="A concise executive paragraph summarizing the investment thesis."
    )


class LiquidityProfile(BaseModel):
    current_price_myr: float
    rsi_14: float | None
    adv_30d: int
    turnover_30d_myr: float
    liquidity_status: str


class DebateSynthesis(BaseModel):
    bull_arguments: list[str]
    bear_arguments: list[str]


class InstitutionalReport(BaseModel):
    target_ticker: str
    target_company: str
    verdict: Literal["STRONG BUY", "ACCUMULATE", "HOLD", "AVOID"]
    intrinsic_value_range_myr: tuple[float | None, float | None]
    valuation_basis: str = "No valuation basis provided."
    valuation_confidence: Literal["low", "medium", "high"] = "low"
    fundamental_findings: list[str]
    liquidity_analysis: LiquidityProfile
    debate: DebateSynthesis
    risk_mitigations: list[str]
    executive_summary: str


class ResearchSourceSummary(BaseModel):
    """Compact runtime provenance for a company research response."""

    filings_used: bool = False
    live_news_used: bool = False
    market_data_used: bool = False
    filing_chunks_returned: int = 0
    notes: list[str] = Field(default_factory=list)


class CompanyResearchResponse(BaseModel):
    """Flexible company research output for question-shaped API requests."""

    target_ticker: str
    target_company: str
    question: str
    intent: str
    answer_markdown: str
    source_summary: ResearchSourceSummary
    structured_report: InstitutionalReport | None = None
