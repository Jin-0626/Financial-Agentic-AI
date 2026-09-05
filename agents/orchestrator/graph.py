import asyncio
import json
import logging
import os
import re
from hashlib import blake2b

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    SubAgent,
    create_deep_agent,
    register_harness_profile,
)
from langchain_core.tools import tool
from langsmith import traceable
from pydantic import BaseModel, Field

from agents.config import default_config
from agents.deepagent_context import MEMORY_PATHS, SKILL_PATHS, build_workspace_backend
from agents.ollama_runtime import build_chat_ollama
from agents.research_planning import (
    CompanyResolutionStatus,
    build_research_plan,
    format_research_plan_for_prompt,
    resolve_company_identity,
)
from agents.schemas.report import (
    AgentAnalysisOutput,
    BullBearAssessment,
    CompanyResearchResponse,
    DebateSynthesis,
    FundamentalFindings,
    InstitutionalReport,
    LiquidityProfile,
    ResearchSourceSummary,
    ValidationReport,
    committee_briefing_format,
)
from agents.tools.bursa_rag import (
    BursaAnnouncementChunk,
    list_indexed_bursa_companies,
    search_bursa_notes,
)
from agents.tools.klse_market_data import (
    fetch_klse_market_snapshot,
    fetch_klse_telemetry,
)
from agents.tools.tavily_search import search_bursa_intelligence

logger = logging.getLogger(__name__)

# Configure LangSmith telemetry in environment
os.environ["LANGCHAIN_TRACING_V2"] = str(default_config.get("langchain_tracing_v2", False)).lower()
os.environ["LANGCHAIN_ENDPOINT"] = str(default_config.get("langchain_endpoint", ""))
os.environ["LANGSMITH_TRACING_V2"] = str(default_config.get("langchain_tracing_v2", False)).lower()
os.environ["LANGSMITH_ENDPOINT"] = str(default_config.get("langchain_endpoint", ""))
os.environ["LANGCHAIN_API_KEY"] = str(default_config.get("langsmith_api_key", ""))
os.environ["LANGSMITH_API_KEY"] = str(default_config.get("langsmith_api_key", ""))
os.environ["LANGCHAIN_PROJECT"] = str(default_config.get("langsmith_project", "Financial Analyst"))
os.environ["LANGSMITH_PROJECT"] = str(default_config.get("langsmith_project", "Financial Analyst"))

register_harness_profile(
    "ollama",
    HarnessProfile(
        excluded_tools=frozenset({"delete", "execute"}),
        excluded_middleware=frozenset({"AnthropicPromptCachingMiddleware"}),
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
    ),
)

# Initialize Remote Ollama Instance
base_llm = build_chat_ollama(temperature=0.1)
structured_llm = base_llm.with_structured_output(AgentAnalysisOutput, method="json_schema")
REPORT_FORMAT_GUIDE = committee_briefing_format().as_prompt()

class BursaRagInput(BaseModel):
    stock_code: str = Field(description="4-digit Bursa stock code, e.g. '0157'")
    query: str = Field(description="Research question or evidence need for Bursa filing retrieval")


class TavilySearchInput(BaseModel):
    stock_code: str = Field(description="4-digit Bursa stock code, e.g. '0157'")
    company_name: str = Field(description="Listed company name, e.g. 'Focus Point Holdings Berhad'")


class KlseMarketInput(BaseModel):
    stock_code: str = Field(description="4-digit Bursa stock code, e.g. '0157'")


class BursaCompanyInput(BaseModel):
    company_query: str = Field(description="Bursa stock name, common name, or 4-digit code")


RAG_QUERY_LIBRARY = {
    "performance": (
    "segmental reporting revenue profit before tax profit after tax current quarter cumulative financial period",
    "review of performance current quarter previous corresponding period revenue PBT PAT",
    ),
    "financial_position": (
    "balance sheet statements of financial position total assets total liabilities equity current assets cash and cash equivalents",
    "borrowings debt securities gearing bank borrowings lease liabilities MFRS 16",
    ),
    "cash_dividend": (
    "cash flow operating investing financing cash and cash equivalents liquidity",
    "dividend interim dividend entitlement date payout prospects Part B",
    ),
    "prospects_risks": (
        "prospects risks material uncertainty Part B",
        "material subsequent events corporate actions announcements",
    ),
}
MAX_RAG_CHUNKS = 10
MAX_RAG_CHARS = 18_000

FUNDAMENTAL_FINDINGS_CONTRACT = (
    "Final response contract: return one JSON object with exactly these top-level keys: "
    "company_name, stock_code, reporting_period, period_notes, evidence, missing_fields, summary. "
    "Each evidence item must contain: claim, value, period, period_basis, source, source_type, confidence. "
    "Do not invent alternative top-level keys such as segment_revenue, segment_profit, cash_position, or dividend_policy."
)

BULL_BEAR_CONTRACT = (
    "Final response contract: return one JSON object with exactly these top-level keys: "
    "bull_arguments, bear_arguments, balanced_view, unsupported_assumptions. "
    "Each argument must be a concise string with its evidence limitation built in when relevant."
)

VALIDATION_CONTRACT = (
    "Final response contract: return one JSON object with exactly these top-level keys: "
    "passed, warnings, unsupported_claims, required_corrections."
)


def _chunk_fingerprint(text: str) -> str:
    normalized = " ".join(text.lower().split())
    return blake2b(normalized.encode("utf-8"), digest_size=12).hexdigest()


def _fit_rag_budget(chunks: list[dict]) -> list[dict]:
    fitted = []
    total_chars = 0
    for chunk in chunks:
        chunk_chars = len(chunk["chunk"])
        if len(fitted) >= MAX_RAG_CHUNKS:
            break
        if fitted and total_chars + chunk_chars > MAX_RAG_CHARS:
            break
        fitted.append(chunk)
        total_chars += chunk_chars
    return fitted


def _select_filing_queries(query: str) -> list[str]:
    """Choose filing retrieval queries proportional to the research question."""
    plan = build_research_plan(query)
    selected = list(plan.filing_queries)
    query_lower = query.lower()

    if any(term in query_lower for term in ("balance sheet", "debt", "borrowings", "cash", "liabilit", "gearing")):
        selected.extend(RAG_QUERY_LIBRARY["financial_position"])
    if any(term in query_lower for term in ("dividend", "cash flow", "liquidity")):
        selected.extend(RAG_QUERY_LIBRARY["cash_dividend"])
    if any(term in query_lower for term in ("prospect", "risk", "subsequent", "corporate action")):
        selected.extend(RAG_QUERY_LIBRARY["prospects_risks"])

    deduped = []
    seen = set()
    for candidate in [query, *selected]:
        normalized = " ".join(candidate.lower().split())
        if normalized in seen:
            continue
        deduped.append(candidate)
        seen.add(normalized)
    return deduped


@tool("query_bursa_quarterly_filings", args_schema=BursaRagInput)
async def query_bursa_quarterly_filings(stock_code: str, query: str) -> str:
    """Retrieve indexed Bursa Malaysia quarterly PDF excerpts from PostgreSQL pgvector."""
    retrieval_queries = _select_filing_queries(query)
    results_by_query = []
    deduped = []
    seen_chunks = set()
    for retrieval_query in retrieval_queries:
        query_results = await search_bursa_notes(stock_code=stock_code, query=retrieval_query, limit=3)
        compact_results = []
        for result in query_results:
            chunk_key = _chunk_fingerprint(result["chunk"])
            chunk_payload = {
                "chunk_id": chunk_key,
                "section": result["section"],
                "fiscal_quarter": result["quarter"],
                "quarter_ended": result["quarter_ended"],
                "similarity": result["similarity"],
                "chunk": " ".join(result["chunk"].split()),
            }
            compact_results.append(chunk_payload)
            if chunk_key not in seen_chunks:
                deduped.append(chunk_payload)
                seen_chunks.add(chunk_key)
        results_by_query.append({"query": retrieval_query, "results": compact_results})

    if not deduped:
        return f"No quarterly disclosure context found in pgvector for stock code {stock_code}."
    fitted_results = _fit_rag_budget(deduped)
    return json.dumps(
        {
            "stock_code": stock_code,
            "primary_query": query,
            "period_warning": (
                "Bursa interim reports often label Q2/Q3 as financial period ended dates; values may be "
                "cumulative year-to-date unless the excerpt explicitly says current quarter. Do not calculate "
                "QoQ changes from Q1 vs Q2 financial-period totals."
            ),
            "coverage": {
                "queries_run": len(retrieval_queries),
                "unique_chunks_found": len(deduped),
                "unique_chunks_returned": len(fitted_results),
                "char_budget": MAX_RAG_CHARS,
                "truncated_by_budget": len(fitted_results) < len(deduped),
            },
            "results": fitted_results,
            "results_by_query": [
                {
                    "query": group["query"],
                    "chunk_ids": [item["chunk_id"] for item in group["results"]],
                }
                for group in results_by_query
            ],
        },
        ensure_ascii=False,
    )


@tool("search_live_bursa_intel", args_schema=TavilySearchInput)
async def search_live_bursa_intel(stock_code: str, company_name: str) -> str:
    """Search live web intelligence for news, announcements, and sentiment on Bursa Malaysia counters."""
    return await search_bursa_intelligence(stock_code=stock_code, company_name=company_name)


@tool("get_klse_market_snapshot", args_schema=KlseMarketInput)
async def get_klse_market_snapshot(stock_code: str) -> str:
    """Fetch verified KLSE price, RSI, volume, turnover, and liquidity telemetry."""
    return await asyncio.to_thread(fetch_klse_market_snapshot, stock_code)


@tool("resolve_bursa_company", args_schema=BursaCompanyInput)
async def resolve_bursa_company(company_query: str) -> str:
    """Resolve common Bursa company names to a stock code and company name."""
    registry_note = "resolver_registry=built_in"
    try:
        indexed_registry = await list_indexed_bursa_companies()
    except Exception as exc:  # noqa: BLE001 - identity resolution should degrade safely if the DB is unavailable.
        indexed_registry = []
        registry_note = f"resolver_registry=built_in; indexed_registry_error={type(exc).__name__}: {exc}"
    if indexed_registry:
        identity = resolve_company_identity(company_query, registry=indexed_registry)
        registry_note = f"resolver_registry=indexed_filings; indexed_companies={len(indexed_registry)}"
        if identity.status is not CompanyResolutionStatus.RESOLVED:
            builtin_identity = resolve_company_identity(company_query)
            if builtin_identity.status is CompanyResolutionStatus.RESOLVED:
                identity = builtin_identity
                registry_note += "; fallback=built_in"
    else:
        identity = resolve_company_identity(company_query)
    lines = [
        f"status={identity.status.value}",
        f"query={identity.query}",
        f"reason={identity.reason}",
        registry_note,
    ]
    if identity.stock_code:
        lines.append(f"stock_code={identity.stock_code}")
    if identity.company_name:
        lines.append(f"company_name={identity.company_name}")
    if identity.sector:
        lines.append(f"sector={identity.sector}")
    if identity.candidates:
        lines.append("candidates=" + "; ".join(identity.candidates))
    if identity.status is not CompanyResolutionStatus.RESOLVED:
        lines.append("action=Do not proceed with expensive company-specific research until identity is clarified or externally verified.")
    return "\n".join(lines)


bursa_tools = [
    resolve_bursa_company,
    query_bursa_quarterly_filings,
    search_live_bursa_intel,
    get_klse_market_snapshot,
]
root_tools = [
    resolve_bursa_company,
    query_bursa_quarterly_filings,
    search_live_bursa_intel,
    get_klse_market_snapshot,
]


# Define Specialized Personas
fundamental_analyst = SubAgent(
    name="bursa_fundamentals_analyst",
    description="Analyzes Bursa Malaysia quarterly notes, MFRS financial disclosures, and cash flow stability.",
    tools=[resolve_bursa_company, query_bursa_quarterly_filings],
    response_format=FundamentalFindings,
    system_prompt=(
        "You are the Bursa fundamentals analyst for an institutional investment committee. "
        "Your job is to extract filing-backed facts, not to write the final recommendation. "
        "If the user provides a company name instead of a code, first resolve it with resolve_bursa_company. "
        "You MUST invoke query_bursa_quarterly_filings before answering. "
        "Analyze only the retrieved Bursa quarterly filing context. Prioritize segment revenue, profit movement, "
        "cash flow, borrowings, MFRS 16 lease liabilities, dividends, prospects, and material subsequent events. "
        "For every exact number, record the value, period, and source in the evidence list. "
        "Set period_basis for each value: use cumulative_period for interim financial-period totals, "
        "current_quarter only when the excerpt explicitly says current quarter, and unknown when unclear. "
        "Separate disclosed facts from inference. If a requested metric is absent, add it to missing_fields; "
        "never fill the gap with market convention, memory, or a plausible estimate. "
        "Be especially careful with Q1/Q2/Q3/Q4 labels, QoQ vs YoY language, and group vs subsidiary figures. "
        "Do not calculate QoQ growth from Q1 versus Q2 interim financial-period totals; Q2 figures may be six-month cumulative values. "
        "If the excerpt says 'compared with the previous corresponding period', use that comparison instead of comparing sequential interim periods. "
        f"{FUNDAMENTAL_FINDINGS_CONTRACT} "
        "Return only the structured response."
    ),
)

market_debater = SubAgent(
    name="bull_bear_debater",
    description="Runs structured dialectical debates between bull and bear investment theses.",
    tools=[],
    response_format=BullBearAssessment,
    system_prompt=(
        "You are a two-sided research debater for Bursa equities. Build a fair bull case and a fair bear case "
        "from the evidence supplied in the task prompt. The bull case should emphasize supported catalysts, "
        "operating momentum, balance-sheet resilience, capital returns, and strategic optionality. The bear case "
        "should emphasize supported margin pressure, leverage, weak cash conversion, execution risk, liquidity risk, "
        "cyclicality, and disclosure gaps. Critically engage with both sides rather than listing generic pros and cons. "
        "Use only supplied evidence. Do not call tools, search, or invent missing figures. "
        "Do not compare Q1 and Q2 as QoQ unless the evidence period_basis is current_quarter for both values. "
        "If an argument depends on an assumption not present in evidence, place it in unsupported_assumptions. "
        "In balanced_view, choose the side with stronger support; say the case is balanced or insufficient when it is. "
        f"{BULL_BEAR_CONTRACT}"
    ),
)

evidence_validator = SubAgent(
    name="evidence_validator",
    description="Validates draft Bursa research claims against supplied evidence and flags unsupported numbers.",
    tools=[],
    response_format=ValidationReport,
    system_prompt=(
        "You are a strict financial evidence validator. Use only the evidence and draft supplied in the task prompt. "
        "Check that company identity, subsidiary identity, reporting period, YoY/QoQ language, dividends, ratios, "
        "lease liabilities, target prices, and exact figures are supported. Return structured validation only. "
        "Mark the report as failed when it states unavailable metrics as facts, mixes reporting periods, "
        "calculates QoQ from cumulative interim-period totals, or converts news snippets into precise valuation/ratio claims without source support. "
        "Also mark the report as failed if the final user-facing draft contains raw source IDs, chunk IDs, URLs, "
        "RAG chunk labels, JSON evidence payloads, or columns named Source/Source(s)/Citation/Chunk/URL. "
        "Do not search, do not infer missing data, and do not soften unsupported claims. "
        f"{VALIDATION_CONTRACT}"
    ),
)

SUPERVISOR_SYSTEM_PROMPT = (
    "You are a general-purpose Bursa Malaysia company research DeepAgent. "
    "Start by understanding the user's actual research question, then decide what evidence is necessary. "
    "Do not force every request through a fixed sequence of fundamentals, valuation, technical analysis, "
    "bull/bear debate, and committee reporting. Use only the tools, skills, and subagents that materially "
    "improve the answer.\n\n"
    "Company identity comes first. Use `resolve_bursa_company` for names, aliases, or stock codes when identity "
    "is not already verified. If the resolver returns ambiguous or unknown, do not silently research a guessed "
    "company; use live search only to clarify identity, or ask for the exact stock code when ambiguity remains.\n\n"
    "Choose sources by claim type. Use Bursa filings for reported financial facts, period comparisons, dividends, "
    "borrowings, cash flow, and management commentary. Use live search for recent developments, news context, "
    "corporate actions, and identity clarification. Use `search_live_bursa_intel` for company news and market "
    "search. Use `get_klse_market_snapshot` for verified KLSE price, RSI, volume, turnover, and liquidity. "
    "For market/news requests, use both Tavily news search and the KLSE market snapshot, mirroring the "
    "TradingAgents split between News Analyst and Market Analyst evidence. When market telemetry is supplied, "
    "include a compact KLSE market snapshot before interpreting recent news or market context.\n\n"
    "Keep research proportional. A latest-results question usually needs current filing evidence and management "
    "commentary. A recent-developments question may need mostly live search and announcement context. A financial "
    "strength or risk question usually needs balance-sheet, cash-flow, debt, and liquidity evidence. A comparison "
    "requires verified identities and comparable periods for each company.\n\n"
    "Separate raw retrieved information, evidence, calculated facts, assumptions, interpretation, and conclusion. "
    "Every exact number must be grounded in supplied evidence. Never invent target prices, valuation ranges, "
    "dividends, ratios, lease liabilities, cash balances, or period labels. Mark them unavailable when unsupported. "
    "Do not calculate QoQ growth from Q1 versus Q2 interim financial-period totals unless both are explicitly "
    "current-quarter values. Default to HOLD when investment evidence is balanced, conflicting, ambiguous, or "
    "insufficient.\n\n"
    "Delegate to `bursa_fundamentals_analyst` when filing extraction or period-basis classification needs specialist "
    "attention. Delegate to `bull_bear_debater` only when a two-sided thesis stress test helps answer the question. "
    "Delegate to `evidence_validator` before final investment briefings or when exact figures, period labels, or "
    "source discipline are material.\n\n"
    "Final output should match the user's requested scope. Use a concise factual answer for narrow questions; use "
    "the committee briefing format only for broad investment-committee style requests:\n"
    f"{REPORT_FORMAT_GUIDE}"
)

API_RESEARCH_SYSTEM_PROMPT = SUPERVISOR_SYSTEM_PROMPT

market_analyst = SubAgent(
    name="market_analyst",
    description="Evaluates KLSE price action, indicators, and market liquidity constraints.",
    system_prompt=(
        "You are a market technician and liquidity risk officer for Bursa Malaysia. "
        "You are provided with a verified market snapshot (Price, RSI, 50 SMA, 30-day ADV, Turnover). "
        "Treat this snapshot as the absolute source of truth: do not claim support/resistance bounces "
        "or exact percentage moves unless directly verified by the numbers. "
        "Evaluate liquidity risk: if 30-day average daily turnover is below RM 200,000, flag it as 'Illiquid/Caution' "
        "and warn of slippage and multi-day execution windows. "
        "Conclude your report with a summary Markdown table of technical indicator readings."
    )
)

bursa_sentiment_analyst = SubAgent(
    name="bursa_sentiment_analyst",
    description="Analyzes pre-fetched institutional news and retail sentiment.",
    system_prompt=(
        "You are a Bursa market sentiment analyst. "
        "Analyze the pre-fetched news headlines (The Edge, StarBiz) and local retail chatter. "
        "No external tools are available; evaluate only the text provided in the prompt. "
        "Identify cross-source divergences (e.g., retail chasing speculative runs while news flow is cautious). "
        "Output an overall_band (Bullish, Mildly Bullish, Neutral, Mixed, Mildly Bearish, Bearish), "
        "a confidence level (low, medium, high based on data density), and a markdown signal table."
    )
)

def _format_filing_context(filing_chunks: list[BursaAnnouncementChunk]) -> str:
    if not filing_chunks:
        return "No indexed quarterly PDF filings found for this counter in pgvector."

    announcements = []
    for index, chunk in enumerate(filing_chunks, start=1):
        chunk_text = chunk["chunk"].replace("\n", "\n> ")
        period_basis_hint = _infer_period_basis_hint(chunk["chunk"])
        announcements.append(
            f"### Announcement {index}\n"
            f"- **Section:** {chunk['section']}\n"
            f"- **Fiscal quarter:** {chunk['quarter']}\n"
            f"- **Quarter ended:** {chunk['quarter_ended']}\n"
            f"- **Period basis hint:** {period_basis_hint}\n"
            f"- **Chunk text:**\n> {chunk_text}"
        )
    return "\n\n".join(announcements)


def _infer_period_basis_hint(chunk_text: str) -> str:
    normalized = " ".join(chunk_text.lower().split())
    if re.search(r"\b(6|six|9|nine|3|three|12|twelve)\s+months?\s+ended\b", normalized):
        if re.search(r"\bfor\s+the\s+3\s+months?\s+ended\b|\bfor\s+the\s+three\s+months?\s+ended\b", normalized):
            return "current_quarter_when_the_specific_value_appears_under_a_3_months_ended_heading; otherwise_check_heading"
        return "cumulative_period"
    if "statement of financial position" in normalized or "total assets" in normalized or "total liabilities" in normalized:
        return "point_in_time"
    if "cash flows" in normalized:
        return "cumulative_period_unless_current_quarter_heading_is_explicit"
    return "unknown"


def _live_news_has_results(news_intel: str) -> bool:
    try:
        payload = json.loads(news_intel)
    except json.JSONDecodeError:
        lowered = news_intel.lower()
        return not any(
            marker in lowered
            for marker in (
                "error fetching",
                "api key not configured",
                "no recent online intelligence",
                "live news was not required",
            )
        )
    if payload.get("ok") is False:
        return False
    return bool(payload.get("results"))


def _fallback_analysis_from_deliberation(deliberation_text: str) -> AgentAnalysisOutput:
    verdict = "HOLD"
    for candidate in ("STRONG BUY", "ACCUMULATE", "AVOID", "HOLD"):
        if re.search(rf"\b{re.escape(candidate)}\b", deliberation_text, re.IGNORECASE):
            verdict = candidate
            break

    fallback_summary = (
        "Structured extraction failed, so the API returned a conservative HOLD response "
        "without inferring detailed financial claims from the unvalidated deliberation."
    )

    return AgentAnalysisOutput(
        verdict=verdict,  # type: ignore[arg-type]
        intrinsic_low=None,
        intrinsic_high=None,
        valuation_basis=(
            "Structured extraction failed; valuation remains unavailable unless explicitly "
            "supported in the source deliberation."
        ),
        valuation_confidence="low",
        fundamental_findings=[
            "Structured extraction failed; detailed filing claims were not inferred."
        ],
        bull_arguments=[],
        bear_arguments=["Structured extraction failed, so unsupported thesis details were not inferred."],
        risk_mitigations=["Review the freeform deliberation and rerun structured extraction before publishing."],
        executive_summary=fallback_summary,
    )


async def _optional_value(coro, fallback):
    if coro is None:
        return fallback
    return await coro


class BursaResearchDesk:
    def __init__(self) -> None:
        self.agent = create_deep_agent(
            model=base_llm,
            tools=root_tools,
            subagents=[fundamental_analyst, market_debater, evidence_validator],
            backend=build_workspace_backend(),
            memory=MEMORY_PATHS,
            skills=SKILL_PATHS,
            system_prompt=API_RESEARCH_SYSTEM_PROMPT,
        )

    @traceable(name="bursa_research_pipeline")
    async def run_research(
        self,
        stock_code: str,
        company_name: str,
        question: str,
        telemetry: LiquidityProfile | None = None,
    ) -> CompanyResearchResponse:
        plan = build_research_plan(question)

        news_task = (
            search_bursa_intelligence(stock_code, company_name)
            if plan.needs_live_news
            else None
        )
        if telemetry is None and plan.needs_market_data:
            try:
                telemetry = await asyncio.to_thread(fetch_klse_telemetry, stock_code)
            except Exception as exc:  # noqa: BLE001 - yfinance transport/cache exceptions vary.
                logger.warning("KLSE market snapshot failed for %s: %s", stock_code, type(exc).__name__)
        filing_query = " ".join(plan.filing_queries) if plan.filing_queries else question
        rag_task = (
            search_bursa_notes(
                stock_code=stock_code,
                query=filing_query,
                limit=6,
            )
            if plan.needs_filings
            else None
        )

        news_intel, filing_chunks = await asyncio.gather(
            _optional_value(news_task, "Live news was not required for this research question."),
            _optional_value(rag_task, []),
        )

        filing_context = _format_filing_context(filing_chunks)
        telemetry_context = (
            (
                f"Price: RM {telemetry.current_price_myr}\n"
                f"RSI(14): {telemetry.rsi_14}\n"
                f"30d Avg Daily Volume: {telemetry.adv_30d:,}\n"
                f"30d Turnover (MYR): RM {telemetry.turnover_30d_myr:,.2f}\n"
                f"Liquidity Category: {telemetry.liquidity_status}"
            )
            if telemetry is not None
            else "Market telemetry was not supplied or not required for this research question."
        )

        prompt = f"""
        User research question: {question}
        Company: {company_name} (KLSE: {stock_code}).

        [DETERMINISTIC RESEARCH PLAN]:
        {format_research_plan_for_prompt(plan)}

        [MARKET TELEMETRY]:
        {telemetry_context}

        [INDEXED BURSA QUARTERLY NOTES (RAG)]:
        {filing_context}

        [LIVE MARKET INTEL]:
        {news_intel}

        If the live market-intel section says the API key is not configured, the search failed,
        or no recent intelligence was found, treat live news as unavailable rather than as
        supporting evidence.

        This request's deterministic intent is {plan.intent.value}. If that intent is not
        broad_analysis, do not produce an investment committee briefing, verdict table,
        target range, or buy/hold/avoid recommendation unless the user explicitly asked for
        an investment committee analysis. Use the smallest answer shape that satisfies the
        question. When market telemetry is supplied, include a compact KLSE market snapshot
        with price, RSI, average volume, turnover, and liquidity status before interpreting
        recent news or market context.

        Treat each filing chunk's period-basis hint as a guardrail. Do not label a value
        as current-quarter when the surrounding heading says six months, nine months, or
        another cumulative interim period unless the exact value is under an explicit
        current-quarter or three-month heading. Do not use quarter-on-quarter language
        unless the evidence contains comparable sequential current-quarter values.

        Answer the user's question directly. Match the shape of the answer to the question:
        use a concise factual answer for narrow questions, a comparison table for comparison
        questions, and the committee briefing format only for broad investment-committee requests.
        Keep raw source IDs, chunk IDs, URLs, JSON evidence objects, and citation columns out of
        the user-facing answer. Distinguish disclosed facts from interpretation, label uncertainty,
        and do not invent unsupported numbers, target prices, ratios, dividends, lease liabilities,
        cash balances, or period labels.
        """

        deliberation = await self.agent.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]}
        )
        answer_markdown = str(deliberation["messages"][-1].content)

        return CompanyResearchResponse(
            target_ticker=f"{stock_code}.KL",
            target_company=company_name,
            question=question,
            intent=plan.intent.value,
            answer_markdown=answer_markdown,
            source_summary=ResearchSourceSummary(
                filings_used=plan.needs_filings,
                live_news_used=plan.needs_live_news and _live_news_has_results(news_intel),
                market_data_used=telemetry is not None,
                filing_chunks_returned=len(filing_chunks),
                notes=list(plan.notes),
            ),
        )

    @traceable(name="bursa_structured_report_pipeline")
    async def run(
        self,
        stock_code: str,
        company_name: str,
        telemetry: LiquidityProfile,
        question: str | None = None,
    ) -> InstitutionalReport:
        research_question = question or f"Analyze {company_name} ({stock_code})."
        research_response = await self.run_research(
            stock_code=stock_code,
            company_name=company_name,
            question=research_question,
            telemetry=telemetry,
        )
        deliberation_text = research_response.answer_markdown

        structured_prompt = f"""
        Convert the following freeform investment committee deliberation into the requested
        structured analysis. Preserve disclosed facts and distinguish them from inference.
        Use HOLD when evidence is balanced, conflicting, ambiguous, or insufficient. Set intrinsic
        valuation fields to null when no source-supported target range exists; do not infer one.
        Do not infer quarter-over-quarter changes from cumulative interim-period totals.
        The source deliberation should follow this report format; preserve its section logic in
        the structured lists and executive summary: {REPORT_FORMAT_GUIDE}
        Remove raw source IDs, chunk IDs, URLs, and citation clutter from user-facing summary text.

        [DELIBERATION]:
        {deliberation_text}
        """
        try:
            analysis = AgentAnalysisOutput.model_validate(
                await structured_llm.ainvoke(structured_prompt)
            )
        except Exception as exc:  # noqa: BLE001 - structured-output exceptions vary by model/provider.
            logger.warning(
                "Structured report extraction failed; using conservative fallback: %s",
                type(exc).__name__,
            )
            analysis = _fallback_analysis_from_deliberation(deliberation_text)

        return InstitutionalReport(
            target_ticker=f"{stock_code}.KL",
            target_company=company_name,
            verdict=analysis.verdict,
            intrinsic_value_range_myr=(analysis.intrinsic_low, analysis.intrinsic_high),
            valuation_basis=analysis.valuation_basis,
            valuation_confidence=analysis.valuation_confidence,
            fundamental_findings=analysis.fundamental_findings,
            liquidity_analysis=telemetry,
            debate=DebateSynthesis(
                bull_arguments=analysis.bull_arguments,
                bear_arguments=analysis.bear_arguments,
            ),
            risk_mitigations=analysis.risk_mitigations,
            executive_summary=analysis.executive_summary,
        )


deep_agent_graph = create_deep_agent(
    model=base_llm,
    tools=root_tools,
    subagents=[fundamental_analyst, market_debater, evidence_validator],
    backend=build_workspace_backend(),
    memory=MEMORY_PATHS,
    skills=SKILL_PATHS,
    system_prompt=SUPERVISOR_SYSTEM_PROMPT,
)
