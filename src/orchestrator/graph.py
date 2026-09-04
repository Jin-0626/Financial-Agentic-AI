import asyncio
import os

from deepagents import SubAgent, create_deep_agent
from langsmith import traceable

from src.config import default_config
from src.ollama_runtime import build_chat_ollama
from src.schemas.report import (
    AgentAnalysisOutput,
    DebateSynthesis,
    InstitutionalReport,
    LiquidityProfile,
    committee_briefing_format,
)
from src.tools.bursa_rag import BursaAnnouncementChunk, search_bursa_notes
from src.tools.tavily_search import search_bursa_intelligence

# Configure LangSmith telemetry in environment
os.environ["LANGCHAIN_TRACING_V2"] = str(default_config.get("langchain_tracing_v2", False)).lower()
os.environ["LANGCHAIN_ENDPOINT"] = str(default_config.get("langchain_endpoint", ""))
os.environ["LANGCHAIN_API_KEY"] = str(default_config.get("langsmith_api_key", ""))
os.environ["LANGCHAIN_PROJECT"] = str(default_config.get("langsmith_project", "Financial Analyst"))

# Initialize Remote Ollama Instance
base_llm = build_chat_ollama(temperature=0.1)
structured_llm = base_llm.with_structured_output(AgentAnalysisOutput, method="json_schema")
REPORT_FORMAT_GUIDE = committee_briefing_format().as_prompt()

# Define Specialized Personas
fundamental_analyst = SubAgent(
    name="bursa_fundamentals_analyst",
    description="Analyzes Bursa Malaysia quarterly notes, MFRS financial disclosures, and cash flow stability.",
    system_prompt=(
        "You are the Bursa fundamentals analyst for an institutional investment committee. "
        "Analyze only the indexed quarterly excerpts supplied in the prompt. "
        "Focus on segment revenue, profit movement, cash flow, capex commitments, MFRS 16 lease adjustments, "
        "borrowings, dividends, and Part B prospects. For every exact number, state the period and source section. "
        "State whether each value is current-quarter, cumulative financial-period, point-in-time, or unclear. "
        "Separate disclosed facts from inference, and list unavailable metrics instead of estimating them. "
        "Be especially careful with Q1/Q2/Q3/Q4 labels, QoQ vs YoY language, and group vs subsidiary figures. "
        "Do not calculate QoQ from Q1 versus Q2 interim financial-period totals unless both are explicitly current-quarter values. "
        "Append a compact Markdown table with metrics, periods, source sections, and implications."
    ),
)

market_debater = SubAgent(
    name="bull_bear_debater",
    description="Runs structured dialectical debates between bull and bear theses.",
    system_prompt=(
        "You are a two-sided Bursa research debater. Build a fair bull case and a fair bear case from supplied "
        "evidence only. The bull case should emphasize supported catalysts, operating momentum, balance-sheet "
        "resilience, capital returns, and strategic optionality. The bear case should emphasize supported margin "
        "pressure, leverage, weak cash conversion, execution risk, liquidity risk, cyclicality, and disclosure gaps. "
        "Critically engage with both sides. Do not invent missing figures, label assumptions explicitly, and do not "
        "treat cumulative Q2 financial-period totals as standalone quarter figures."
    ),
)

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
        announcements.append(
            f"### Announcement {index}\n"
            f"- **Section:** {chunk['section']}\n"
            f"- **Fiscal quarter:** {chunk['quarter']}\n"
            f"- **Chunk text:**\n> {chunk_text}"
        )
    return "\n\n".join(announcements)


class BursaResearchDesk:
    def __init__(self) -> None:
        self.agent = create_deep_agent(
            model=base_llm,
            subagents=[fundamental_analyst, market_debater],
            system_prompt=(
                "You are the Investment Committee Lead for a Malaysian institutional asset manager. "
                "Synthesize findings from your subagents, filing excerpts, and market telemetry into "
                "a detailed freeform institutional research deliberation. Do not output JSON; explain "
                "the evidence, disagreements, and final reasoning in Markdown. Default to HOLD when "
                "the evidence is balanced, conflicting, ambiguous, or insufficient. Do not present a "
                "target range, ratio, dividend, lease liability, or period label unless it is supported "
                "by supplied evidence; say unavailable when support is missing. Do not calculate QoQ from "
                "Q1 versus Q2 cumulative interim-period totals. Follow this report layout when drafting: "
                f"{REPORT_FORMAT_GUIDE} Keep raw source IDs, chunk IDs, URLs, and citation columns out of "
                "the user-facing report."
            ),
        )

    @traceable(name="bursa_research_pipeline")
    async def run(
        self, stock_code: str, company_name: str, telemetry: LiquidityProfile
    ) -> InstitutionalReport:
        # Step 1: Concurrently fetch Tavily web intelligence and PostgreSQL pgvector RAG chunks
        news_task = search_bursa_intelligence(stock_code, company_name)
        rag_task = search_bursa_notes(
            stock_code=stock_code,
            query=(
                "balance sheet statements of financial position total assets total liabilities equity "
                "cash and cash equivalents operating cash flows MFRS 16 lease liabilities borrowings dividends prospects"
            ),
            limit=6,
        )

        news_intel, filing_chunks = await asyncio.gather(news_task, rag_task)

        filing_context = _format_filing_context(filing_chunks)

        # Step 2: Ask DeepAgents for freeform reasoning before schema-constrained extraction.
        prompt = f"""
        Conduct an institutional research assessment for: {company_name} (KLSE: {stock_code}).
        
        [MARKET TELEMETRY]:
        Price: RM {telemetry.current_price_myr}
        RSI(14): {telemetry.rsi_14}
        30d Avg Daily Volume: {telemetry.adv_30d:,}
        30d Turnover (MYR): RM {telemetry.turnover_30d_myr:,.2f}
        Liquidity Category: {telemetry.liquidity_status}
        
        [INDEXED BURSA QUARTERLY NOTES (RAG)]:
        {filing_context}
        
        [LIVE MARKET INTEL (TAVILY)]:
        {news_intel}
        
        Evaluate the indexed announcements specifically for MFRS 16 lease liabilities, debt and
        borrowings, and Part B prospects. Distinguish disclosed facts from inference and reflect
        material findings in the fundamental_findings and risk_mitigations fields.

        Produce a freeform Markdown deliberation covering the evidence, bull and bear cases,
        valuation range if supported, liquidity constraints, and the requested filing-specific risks.
        If valuation evidence is insufficient, state that the target range is unavailable and explain
        what data is missing. Do not calculate QoQ from Q1 versus Q2 financial-period totals unless
        both values are explicitly current-quarter values.

        [REQUIRED REPORT FORMAT]:
        {REPORT_FORMAT_GUIDE}

        Do not include raw source IDs, chunk IDs, URLs, JSON evidence objects, or source/citation columns
        in the user-facing report. Keep source details internal and express support as concise evidence
        basis wording when needed.
        """

        deliberation = await self.agent.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]}
        )
        deliberation_text = str(deliberation["messages"][-1].content)

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
        analysis = AgentAnalysisOutput.model_validate(
            await structured_llm.ainvoke(structured_prompt)
        )

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
