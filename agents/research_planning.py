from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum


class ResearchIntent(StrEnum):
    BROAD_ANALYSIS = "broad_analysis"
    LATEST_RESULTS = "latest_results"
    RECENT_DEVELOPMENTS = "recent_developments"
    RISK_REVIEW = "risk_review"
    HISTORICAL_PERFORMANCE = "historical_performance"
    COMPARISON = "comparison"
    FILING_REVIEW = "filing_review"
    FINANCIAL_STRENGTH = "financial_strength"


class CompanyResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CompanyIdentity:
    query: str
    status: CompanyResolutionStatus
    stock_code: str | None = None
    company_name: str | None = None
    sector: str | None = None
    reason: str = ""
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompanyRegistryEntry:
    stock_code: str
    company_name: str
    aliases: tuple[str, ...] = ()
    sector: str | None = None


@dataclass(frozen=True)
class ResearchPlan:
    question: str
    intent: ResearchIntent
    evidence_requirements: tuple[str, ...]
    filing_queries: tuple[str, ...] = ()
    needs_filings: bool = False
    needs_live_news: bool = False
    needs_market_data: bool = False
    needs_comparison: bool = False
    needs_subagent_debate: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)


BUILTIN_COMPANY_REGISTRY = (
    CompanyRegistryEntry(
        stock_code="0157",
        company_name="Focus Point Holdings Berhad",
        aliases=("Focus Point", "FOCUSP", "Focus Point Holdings"),
        sector="Retail optical and Komugi bakery",
    ),
)


def normalize_company_query(company_query: str) -> str:
    return re.sub(r"[^a-z0-9]", "", company_query.lower())


def normalize_bursa_stock_code(stock_code: str) -> str:
    cleaned = stock_code.strip().upper().removesuffix(".KL")
    if not re.fullmatch(r"\d{4}[A-Z]?", cleaned):
        raise ValueError(f"Invalid Bursa stock code: {stock_code!r}")
    return cleaned


def _entry_match_keys(entry: CompanyRegistryEntry) -> set[str]:
    names = {entry.stock_code, entry.company_name, *entry.aliases}
    keys = {normalize_company_query(name) for name in names if name}
    return {key for key in keys if key}


def _format_candidate(entry: CompanyRegistryEntry) -> str:
    return f"{entry.stock_code} - {entry.company_name}"


def resolve_company_identity(
    company_query: str,
    registry: Iterable[CompanyRegistryEntry] = BUILTIN_COMPANY_REGISTRY,
) -> CompanyIdentity:
    normalized = normalize_company_query(company_query)
    if not normalized:
        return CompanyIdentity(
            query=company_query,
            status=CompanyResolutionStatus.UNKNOWN,
            reason="Empty company query.",
        )

    entries = tuple(registry)
    exact_matches = [
        entry for entry in entries if normalized in _entry_match_keys(entry)
    ]
    if len(exact_matches) == 1:
        partial_alternatives = [
            entry
            for entry in entries
            if entry not in exact_matches
            and len(normalized) >= 4
            and any(normalized in key for key in _entry_match_keys(entry))
        ]
        if partial_alternatives:
            candidates = [*exact_matches, *partial_alternatives]
            return CompanyIdentity(
                query=company_query,
                status=CompanyResolutionStatus.AMBIGUOUS,
                reason="Company query matched one registry alias exactly but also matched other company names.",
                candidates=tuple(_format_candidate(entry) for entry in candidates),
            )
        entry = exact_matches[0]
        return CompanyIdentity(
            query=company_query,
            status=CompanyResolutionStatus.RESOLVED,
            stock_code=entry.stock_code,
            company_name=entry.company_name,
            sector=entry.sector,
            reason="Matched local Bursa company registry entry.",
        )
    if len(exact_matches) > 1:
        return CompanyIdentity(
            query=company_query,
            status=CompanyResolutionStatus.AMBIGUOUS,
            reason="Company query matched multiple local registry entries.",
            candidates=tuple(_format_candidate(entry) for entry in exact_matches),
        )

    partial_matches = [
        entry
        for entry in entries
        if len(normalized) >= 4
        and any(
            normalized in key or key in normalized for key in _entry_match_keys(entry)
        )
    ]
    if len(partial_matches) == 1:
        entry = partial_matches[0]
        return CompanyIdentity(
            query=company_query,
            status=CompanyResolutionStatus.RESOLVED,
            stock_code=entry.stock_code,
            company_name=entry.company_name,
            sector=entry.sector,
            reason="Matched local Bursa company registry entry by partial name.",
        )
    if len(partial_matches) > 1:
        return CompanyIdentity(
            query=company_query,
            status=CompanyResolutionStatus.AMBIGUOUS,
            reason="Company query partially matched multiple local registry entries.",
            candidates=tuple(_format_candidate(entry) for entry in partial_matches),
        )

    if normalized.isdigit() and len(normalized) == 4:
        return CompanyIdentity(
            query=company_query,
            status=CompanyResolutionStatus.RESOLVED,
            stock_code=normalized,
            company_name=None,
            reason="Valid Bursa stock code supplied, but local registry does not know the official name.",
        )

    if len(normalized) <= 3:
        return CompanyIdentity(
            query=company_query,
            status=CompanyResolutionStatus.AMBIGUOUS,
            reason="Short company names or aliases can match multiple listed entities.",
        )

    return CompanyIdentity(
        query=company_query,
        status=CompanyResolutionStatus.UNKNOWN,
        reason="No local Bursa registry match. Use live search or ask for the exact stock code before expensive research.",
    )


def infer_research_intent(question: str) -> ResearchIntent:
    text = question.lower()
    if any(
        word in text
        for word in ("compare", "versus", " vs ", "stronger than", "weaker than")
    ):
        return ResearchIntent.COMPARISON
    if any(
        word in text
        for word in (
            "latest result",
            "latest results",
            "quarterly result",
            "earnings",
            "decline",
        )
    ):
        return ResearchIntent.LATEST_RESULTS
    if any(
        word in text
        for word in ("recent", "changed", "development", "announcement", "news")
    ):
        return ResearchIntent.RECENT_DEVELOPMENTS
    if any(word in text for word in ("risk", "risks", "threat", "concern")):
        return ResearchIntent.RISK_REVIEW
    if any(
        word in text
        for word in (
            "filing",
            "financial statement",
            "financial statements",
            "annual report",
            "quarterly report",
            "what does",
            "fetch the full",
        )
    ):
        return ResearchIntent.FILING_REVIEW
    if any(
        word in text
        for word in (
            "several years",
            "historical",
            "over the years",
            "trend",
            "performed",
        )
    ):
        return ResearchIntent.HISTORICAL_PERFORMANCE
    if any(
        word in text
        for word in (
            "financially stronger",
            "balance sheet",
            "cash flow",
            "debt",
            "gearing",
        )
    ):
        return ResearchIntent.FINANCIAL_STRENGTH
    return ResearchIntent.BROAD_ANALYSIS


def build_research_plan(question: str) -> ResearchPlan:
    intent = infer_research_intent(question)

    by_intent: dict[
        ResearchIntent,
        tuple[tuple[str, ...], tuple[str, ...], bool, bool, bool, bool, bool],
    ] = {
        ResearchIntent.LATEST_RESULTS: (
            (
                "latest reporting period",
                "reported revenue/profit movement",
                "management commentary",
                "cash flow and balance-sheet changes when material",
            ),
            (
                "latest quarterly result revenue profit current quarter cumulative financial period management commentary",
                "review of performance current quarter previous corresponding period revenue PBT PAT",
            ),
            True,
            True,
            True,
            False,
            False,
        ),
        ResearchIntent.RECENT_DEVELOPMENTS: (
            (
                "recent announcements",
                "publication dates",
                "event dates",
                "market/news context",
            ),
            ("material subsequent events prospects announcements corporate actions",),
            False,
            True,
            True,
            False,
            False,
        ),
        ResearchIntent.RISK_REVIEW: (
            (
                "risk factors",
                "balance sheet pressure",
                "cash generation",
                "industry/company developments",
            ),
            (
                "borrowings debt securities gearing bank borrowings lease liabilities MFRS 16",
                "cash flow operating investing financing cash and cash equivalents liquidity",
                "prospects risks material uncertainty Part B",
            ),
            True,
            True,
            True,
            False,
            True,
        ),
        ResearchIntent.HISTORICAL_PERFORMANCE: (
            (
                "multi-period filings",
                "operating trend",
                "profitability trend",
                "cash generation trend",
            ),
            (
                "historical revenue profit financial performance annual quarterly trend",
                "cash flow operating cash generation balance sheet trend",
            ),
            True,
            False,
            False,
            False,
            False,
        ),
        ResearchIntent.COMPARISON: (
            (
                "correct identity for each company",
                "comparable periods",
                "financial strength",
                "business model differences",
            ),
            (
                "revenue profit balance sheet cash borrowings comparable period",
                "business segments prospects risks",
            ),
            True,
            True,
            True,
            True,
            True,
        ),
        ResearchIntent.FILING_REVIEW: (
            (
                "relevant filing",
                "reported facts",
                "period basis",
                "management commentary",
                "missing fields",
            ),
            (
                "latest filing financial statements notes prospects dividends borrowings cash flow",
                "review of performance current quarter previous corresponding period revenue PBT PAT",
            ),
            True,
            False,
            False,
            False,
            False,
        ),
        ResearchIntent.FINANCIAL_STRENGTH: (
            ("balance sheet", "cash", "borrowings/debt", "cash flow", "liquidity"),
            (
                "balance sheet statements of financial position total assets total liabilities equity current assets cash and cash equivalents",
                "borrowings debt securities gearing bank borrowings lease liabilities MFRS 16",
                "cash flow operating investing financing cash and cash equivalents liquidity",
            ),
            True,
            False,
            False,
            False,
            True,
        ),
        ResearchIntent.BROAD_ANALYSIS: (
            (
                "company identity",
                "business model",
                "latest filings",
                "recent developments",
                "key risks",
            ),
            (
                "segmental reporting revenue profit before tax profit after tax current quarter cumulative financial period",
                "balance sheet statements of financial position total assets total liabilities equity current assets cash and cash equivalents",
                "cash flow operating investing financing cash and cash equivalents liquidity",
                "dividend interim dividend entitlement date payout prospects Part B",
            ),
            True,
            True,
            True,
            False,
            True,
        ),
    }

    (
        requirements,
        queries,
        needs_filings,
        needs_live_news,
        needs_market_data,
        needs_comparison,
        needs_debate,
    ) = by_intent[intent]
    return ResearchPlan(
        question=question,
        intent=intent,
        evidence_requirements=requirements,
        filing_queries=queries,
        needs_filings=needs_filings,
        needs_live_news=needs_live_news,
        needs_market_data=needs_market_data,
        needs_comparison=needs_comparison,
        needs_subagent_debate=needs_debate,
        notes=(
            "Research depth should stop when the evidence is sufficient for the user's question.",
            "Do not force valuation, technical analysis, or bull/bear debate unless relevant to the question.",
        ),
    )


def format_research_plan_for_prompt(plan: ResearchPlan) -> str:
    lines = [
        f"Research intent: {plan.intent.value}",
        "Evidence requirements:",
        *[f"- {requirement}" for requirement in plan.evidence_requirements],
        "Capability guidance:",
        f"- filings: {'yes' if plan.needs_filings else 'optional'}",
        f"- live news/search: {'yes' if plan.needs_live_news else 'optional'}",
        f"- market data: {'yes' if plan.needs_market_data else 'optional'}",
        f"- comparison: {'yes' if plan.needs_comparison else 'no'}",
        f"- bull/bear debate: {'yes' if plan.needs_subagent_debate else 'only if it improves the answer'}",
    ]
    return "\n".join(lines)
