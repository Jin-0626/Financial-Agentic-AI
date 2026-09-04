import json
from hashlib import blake2b

# src/studio_graph.py
from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    SubAgent,
    create_deep_agent,
    register_harness_profile,
)
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.config import default_config
from src.ollama_runtime import build_chat_ollama
from src.schemas.report import (
    BullBearAssessment,
    FundamentalFindings,
    ValidationReport,
    committee_briefing_format,
)
from src.tools.bursa_rag import search_bursa_notes
from src.tools.tavily_search import search_bursa_intelligence

base_url = str(default_config.get("ollama_base_url", "http://localhost:11434")).rstrip("/")
model_name = str(default_config.get("primary_model", "gpt-oss:120b"))
# 1. LLM Initialization
register_harness_profile(
    "ollama",
    HarnessProfile(
        excluded_tools=frozenset(
            {"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep", "execute"}
        ),
        excluded_middleware=frozenset({"AnthropicPromptCachingMiddleware"}),
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
    ),
)
base_llm = build_chat_ollama(temperature=0.1)

# 2. Tool Definitions
class BursaRagInput(BaseModel):
    stock_code: str = Field(description="4-digit Bursa stock code, e.g. '0157'")
    query: str = Field(description="Query focusing on prospects, optical retail, bakery, lease debt, dividends")


RAG_SUPPLEMENTAL_QUERIES = (
    "segmental reporting revenue profit before tax profit after tax current quarter cumulative financial period",
    "review of performance current quarter previous corresponding period revenue PBT PAT",
    "balance sheet statements of financial position total assets total liabilities equity current assets cash and cash equivalents",
    "borrowings debt securities gearing bank borrowings lease liabilities MFRS 16",
    "cash flow operating investing financing cash and cash equivalents liquidity",
    "dividend interim dividend entitlement date payout prospects Part B",
)
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

REPORT_FORMAT_GUIDE = committee_briefing_format().as_prompt()


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


@tool("query_bursa_quarterly_filings", args_schema=BursaRagInput)
async def query_bursa_quarterly_filings(stock_code: str, query: str) -> str:
    """Retrieve indexed Bursa Malaysia quarterly PDF excerpts (Part A, Part B, MFRS 16) from PostgreSQL pgvector."""
    retrieval_queries = [query, *RAG_SUPPLEMENTAL_QUERIES]
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

class TavilySearchInput(BaseModel):
    stock_code: str = Field(description="4-digit Bursa stock code, e.g. '0157'")
    company_name: str = Field(description="Listed company name, e.g. 'Focus Point Holdings Berhad'")

@tool("search_live_bursa_intel", args_schema=TavilySearchInput)
async def search_live_bursa_intel(stock_code: str, company_name: str) -> str:
    """Search live web intelligence for news, announcements, and sentiment on Bursa Malaysia counters."""
    return await search_bursa_intelligence(stock_code=stock_code, company_name=company_name)

class BursaCompanyInput(BaseModel):
    company_query: str = Field(description="Bursa stock name, common name, or 4-digit code")


@tool("resolve_bursa_company", args_schema=BursaCompanyInput)
async def resolve_bursa_company(company_query: str) -> str:
    """Resolve common Bursa company names to a stock code and company name."""
    normalized = company_query.lower().replace(" ", "").replace("-", "")
    if normalized in {"focuspoint", "focusp", "focuspointholdings", "focuspointholdingsberhad", "0157"}:
        return "stock_code=0157\ncompany_name=Focus Point Holdings Berhad\nsector=Retail optical and Komugi bakery"
    if normalized.isdigit() and len(normalized) == 4:
        return f"stock_code={normalized}\ncompany_name=Unknown Bursa counter; use search_live_bursa_intel to identify it."
    return (
        "No local resolver match. Use search_live_bursa_intel with the user's company query, "
        "then ask for the exact Bursa stock code if the result is ambiguous."
    )


bursa_tools = [resolve_bursa_company, query_bursa_quarterly_filings, search_live_bursa_intel]
root_tools = [resolve_bursa_company, query_bursa_quarterly_filings, search_live_bursa_intel]

# 3. SubAgent Definitions
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
    "You are the Investment Committee Lead for Malaysian institutional equities.\n"
    "Whenever analyzing a stock (e.g. '0157' or 'YTL'), execute this sequence strictly:\n\n"
    "1. RESOLUTION:\n"
    "   - Call `resolve_bursa_company` to obtain the verified 4-digit stock code and sector.\n\n"
    "2. GROUNDING (Execute BOTH tools before delegating):\n"
    "   - Call `query_bursa_quarterly_filings` with stock_code and query='segment revenue optical f&b profit lease debt'.\n"
    "   - Call `search_live_bursa_intel` with stock_code and company_name.\n\n"
    "3. FUNDAMENTAL ANALYSIS:\n"
    "   - Delegate to `bursa_fundamentals_analyst`. YOU MUST pass the retrieved filings and search context "
    "directly into the task description.\n\n"
    "4. BULL/BEAR DEBATE:\n"
    "   - Delegate to `bull_bear_debater`. YOU MUST include the JSON output from `bursa_fundamentals_analyst` "
    "in the task description.\n\n"
    "5. COMPLIANCE VALIDATION:\n"
    "   - Construct a draft report.\n"
    "   - Delegate to `evidence_validator` with subagent_type='evidence_validator'. "
    "YOU MUST include BOTH the draft text AND the structured evidence in the task description.\n\n"
    "6. FINAL REPORT:\n"
    f"{REPORT_FORMAT_GUIDE}\n"
    "\nRating discipline:\n"
    "   - STRONG BUY: filing evidence and recent intel strongly support upside and risks are manageable.\n"
    "   - ACCUMULATE: constructive evidence, but risks or disclosure gaps remain.\n"
    "   - HOLD: balanced, conflicting, ambiguous, or insufficient evidence.\n"
    "   - AVOID: adverse evidence, weak disclosure quality, or unsupported risk/valuation profile.\n"
    "Default to HOLD when evidence is insufficient; do not manufacture a directional call to appear decisive. "
    "Never present an invented target price, ratio, period label, dividend, lease liability, or cash-flow figure. "
    "Do not calculate QoQ growth from Q1 versus Q2 interim financial-period totals unless evidence explicitly marks both as current-quarter values. "
    "Do not say Q2 cumulative values doubled from Q1; instead state that Q2 financial-period totals are cumulative and cite the filing's own previous-corresponding-period comparisons. "
    "If a subagent returns malformed JSON or says it lacks supplied evidence, rerun that subagent once with the exact evidence pasted in and the required schema contract. "
    "If validation flags source-clutter issues, remove the raw source IDs/URLs/chunk labels from the final report while preserving the supported facts. "
    "If validation flags factual issues, correct the final report or include the warnings explicitly. "
    "Complete all 5 steps before producing the final answer."
)


# 4. Supervisor Graph (Bind tools at the root level as well)
deep_agent_graph = create_deep_agent(
    model=base_llm,
    tools=root_tools,
    subagents=[fundamental_analyst, market_debater, evidence_validator],
    system_prompt=SUPERVISOR_SYSTEM_PROMPT,
)
