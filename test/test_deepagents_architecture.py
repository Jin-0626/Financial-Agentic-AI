import asyncio
import json
from datetime import date

import pandas as pd
import pytest
from fastapi import HTTPException

from agents.deepagent_context import MEMORY_PATHS, SKILL_PATHS, build_workspace_backend
from agents.main import ResearchRequest, app, execute_analysis, execute_research
from agents.orchestrator import graph as orchestrator_graph
from agents.research_planning import (
    CompanyRegistryEntry,
    CompanyResolutionStatus,
    ResearchIntent,
    build_research_plan,
    normalize_bursa_stock_code,
    resolve_company_identity,
)
from agents.schemas.report import (
    AgentAnalysisOutput,
    CompanyResearchResponse,
    EvidenceItem,
    FundamentalFindings,
    ResearchSourceSummary,
    ValidationReport,
    committee_briefing_format,
)
from agents.tools import bursa_rag, klse_market_data, tavily_search


def test_analysis_output_allows_missing_valuation_range() -> None:
    analysis = AgentAnalysisOutput(
        verdict="HOLD",
        intrinsic_low=None,
        intrinsic_high=None,
        valuation_basis="No peer multiple or DCF evidence was supplied.",
        valuation_confidence="low",
        fundamental_findings=["Q1 filing evidence is available, Q2 is not."],
        bull_arguments=[],
        bear_arguments=["Valuation support is insufficient."],
        risk_mitigations=["Do not publish a target price without source evidence."],
        executive_summary="Hold pending verified valuation evidence.",
    )

    assert analysis.intrinsic_low is None
    assert analysis.intrinsic_high is None
    assert analysis.valuation_confidence == "low"


def test_evidence_and_validation_schemas_capture_unsupported_claims() -> None:
    findings = FundamentalFindings(
        company_name="Focus Point Holdings Berhad",
        stock_code="0157",
        reporting_period="Q1 2026",
        evidence=[
            EvidenceItem(
                claim="Optical revenue was disclosed in the retrieved filing excerpt.",
                value="RM66.808m",
                period="Q1 2026",
                source="RAG chunk: General Notes | Q1 2026",
                source_type="filing",
                confidence="high",
            )
        ],
        missing_fields=["Q2 2026 net profit", "lease liabilities"],
        summary="The available excerpt supports Q1 segment revenue only.",
    )
    validation = ValidationReport(
        passed=False,
        unsupported_claims=["Q2 FY2026 revenue table"],
        required_corrections=["Rename the period to Q1 2026 or retrieve Q2 evidence."],
    )

    assert findings.missing_fields == ["Q2 2026 net profit", "lease liabilities"]
    assert validation.passed is False
    assert "Q2 FY2026 revenue table" in validation.unsupported_claims


def test_tavily_search_returns_compact_json(monkeypatch) -> None:
    class FakeClient:
        async def search(self, **kwargs):
            assert kwargs["max_results"] == 3
            return {
                "results": [
                    {
                        "title": "A" * 250,
                        "url": "https://example.test/a",
                        "content": "word " * 300,
                        "published_date": "2026-09-04",
                    }
                ]
            }

    monkeypatch.setattr(tavily_search, "tavily_client", FakeClient())

    payload = asyncio.run(
        tavily_search.search_bursa_intelligence("0157", "Focus Point")
    )
    data = json.loads(payload)

    assert data["ok"] is True
    assert data["source"] == "tavily"
    assert data["stock_code"] == "0157"
    assert len(data["results"]) == 1
    assert len(data["results"][0]["title"]) <= 180
    assert len(data["results"][0]["snippet"]) <= 700


def test_tavily_search_returns_structured_failure(monkeypatch) -> None:
    class FakeClient:
        async def search(self, **kwargs):
            raise TimeoutError("network timeout")

    monkeypatch.setattr(tavily_search, "tavily_client", FakeClient())

    payload = asyncio.run(
        tavily_search.search_bursa_intelligence("0157", "Focus Point")
    )
    data = json.loads(payload)

    assert data["ok"] is False
    assert data["source"] == "tavily"
    assert data["error_type"] == "TimeoutError"
    assert data["results"] == []


def test_klse_market_snapshot_returns_compact_json(monkeypatch) -> None:
    monkeypatch.setattr(
        klse_market_data,
        "fetch_klse_telemetry",
        lambda stock_code: klse_market_data.LiquidityProfile(
            current_price_myr=0.52,
            rsi_14=55.0,
            adv_30d=100000,
            turnover_30d_myr=52000.0,
            liquidity_status="Illiquid/Caution",
        ),
    )

    payload = klse_market_data.fetch_klse_market_snapshot("0157")
    data = json.loads(payload)

    assert data["ok"] is True
    assert data["source"] == "klse_yfinance"
    assert data["ticker"] == "0157.KL"
    assert data["current_price_myr"] == 0.52


def test_klse_telemetry_uses_latest_valid_close_when_latest_row_is_nan(
    monkeypatch,
) -> None:
    frame = pd.DataFrame(
        {
            "Close": [0.50, 0.51, None],
            "Volume": [1000, 2000, 3000],
        }
    )
    monkeypatch.setattr(klse_market_data.yf, "download", lambda *args, **kwargs: frame)

    telemetry = klse_market_data.fetch_klse_telemetry("0157")

    assert telemetry.current_price_myr == 0.51
    assert telemetry.adv_30d == 1500
    assert telemetry.turnover_30d_myr == 760.0


def test_balance_sheet_chunks_are_classified_as_financial_position() -> None:
    section = bursa_rag.classify_section(
        "Condensed consolidated statements of financial position TOTAL ASSETS "
        "TOTAL EQUITY AND LIABILITIES Lease liabilities"
    )

    assert section == "Financial Position Statement"


def test_bursa_report_chunking_preserves_full_financial_statement_sections() -> None:
    text = """
    Cover page
    CONDENSED CONSOLIDATED STATEMENTS OF COMPREHENSIVE INCOME
    Individual quarter Cumulative quarter
    Revenue 100 90 200 180
    CONDENSED CONSOLIDATED STATEMENTS OF FINANCIAL POSITION
    ASSETS
    TOTAL ASSETS 300
    CONDENSED CONSOLIDATED STATEMENTS OF CASH FLOWS
    For the 6 months ended
    Net cash from operating activities 50
    CONDENSED CONSOLIDATED STATEMENTS OF CASH FLOWS (cont'd)
    Cash and cash equivalents 80
    Part A - Explanatory notes pursuant to Malaysian Financial Reporting Standard
    condensed consolidated statements of financial position should be read in conjunction
    with the annual financial statements.
    """

    chunks = bursa_rag.chunk_bursa_report_text(text)
    sections = [section for section, _ in chunks]
    cash_chunks = [
        chunk for section, chunk in chunks if section == "Cash Flow Statement"
    ]

    assert sections.count("Comprehensive Income Statement") == 1
    assert sections.count("Financial Position Statement") == 1
    assert sections.count("Cash Flow Statement") == 1
    assert "Cash and cash equivalents 80" in cash_chunks[0]
    assert any(section == "Part A - Explanatory Notes" for section in sections)


def test_balance_sheet_search_adds_lexical_statement_matches(monkeypatch) -> None:
    class FakeEmbeddings:
        async def aembed_query(self, query):
            assert "financial position" in query
            return [0.1, 0.2, 0.3]

    class FakeConnection:
        def __init__(self):
            self.calls = []

        async def fetch(self, sql, *args):
            self.calls.append((sql, args))
            if "embedding <=>" in sql:
                return [
                    {
                        "content_chunk": "Segmental revenue details",
                        "section_category": "General Notes",
                        "fiscal_quarter": "Q2 2026",
                        "quarter_ended": date(2026, 6, 30),
                        "similarity_score": 0.51,
                    }
                ]
            return [
                {
                    "content_chunk": "TOTAL ASSETS 346,086 TOTAL LIABILITIES 185,062",
                    "section_category": "Financial Position Statement",
                    "fiscal_quarter": "Q2 2026",
                    "quarter_ended": date(2026, 6, 30),
                    "similarity_score": 1.0,
                }
            ]

        async def close(self):
            pass

    fake_connection = FakeConnection()

    async def fake_connect(database_url):
        assert database_url
        return fake_connection

    monkeypatch.setattr(bursa_rag, "embeddings_client", FakeEmbeddings())
    monkeypatch.setattr(bursa_rag.asyncpg, "connect", fake_connect)

    results = asyncio.run(
        bursa_rag.search_bursa_notes(
            "0157",
            "balance sheet statements of financial position total assets total liabilities",
            limit=3,
        )
    )

    assert len(fake_connection.calls) == 3
    assert any("TOTAL ASSETS" in result["chunk"] for result in results)
    assert any(
        result["section"] == "Financial Position Statement" for result in results
    )


def test_orchestrator_graph_prompts_keep_filing_tool_and_evidence_discipline() -> None:
    main_tool_names = {tool.name for tool in orchestrator_graph.root_tools}

    assert "query_bursa_quarterly_filings" in main_tool_names
    assert "search_live_bursa_intel" in main_tool_names
    assert "get_klse_market_snapshot" in main_tool_names
    assert orchestrator_graph.MEMORY_PATHS == ["/memories/AGENTS.md"]
    assert orchestrator_graph.SKILL_PATHS == ["/skills/"]
    assert (
        "general-purpose Bursa Malaysia company research DeepAgent"
        in orchestrator_graph.SUPERVISOR_SYSTEM_PROMPT
    )
    assert (
        "Do not force every request through a fixed sequence"
        in orchestrator_graph.SUPERVISOR_SYSTEM_PROMPT
    )
    assert "Company identity comes first" in orchestrator_graph.SUPERVISOR_SYSTEM_PROMPT
    assert "Choose sources by claim type" in orchestrator_graph.SUPERVISOR_SYSTEM_PROMPT
    assert (
        "TradingAgents split between News Analyst and Market Analyst evidence"
        in orchestrator_graph.SUPERVISOR_SYSTEM_PROMPT
    )
    assert "compact KLSE market snapshot" in orchestrator_graph.SUPERVISOR_SYSTEM_PROMPT
    assert "Keep research proportional" in orchestrator_graph.SUPERVISOR_SYSTEM_PROMPT
    assert (
        "Separate raw retrieved information, evidence, calculated facts"
        in orchestrator_graph.SUPERVISOR_SYSTEM_PROMPT
    )
    assert (
        "Default to HOLD when investment evidence is balanced"
        in orchestrator_graph.SUPERVISOR_SYSTEM_PROMPT
    )
    assert (
        "committee briefing format only for broad"
        in orchestrator_graph.SUPERVISOR_SYSTEM_PROMPT
    )
    assert (
        "For every exact number"
        in orchestrator_graph.fundamental_analyst["system_prompt"]
    )
    assert "period_basis" in orchestrator_graph.fundamental_analyst["system_prompt"]
    assert (
        "exactly these top-level keys"
        in orchestrator_graph.fundamental_analyst["system_prompt"]
    )
    assert (
        "unsupported_assumptions" in orchestrator_graph.market_debater["system_prompt"]
    )
    assert (
        "mixes reporting periods"
        in orchestrator_graph.evidence_validator["system_prompt"]
    )


def test_api_and_studio_use_same_adaptive_research_prompt() -> None:
    assert (
        orchestrator_graph.API_RESEARCH_SYSTEM_PROMPT
        == orchestrator_graph.SUPERVISOR_SYSTEM_PROMPT
    )
    assert (
        "Investment Committee Lead for a Malaysian institutional asset manager"
        not in orchestrator_graph.API_RESEARCH_SYSTEM_PROMPT
    )


def test_memory_skill_backend_exposes_only_agent_memory_and_skills() -> None:
    backend = build_workspace_backend()

    memory = backend.read(MEMORY_PATHS[0])
    skills = backend.ls(SKILL_PATHS[0])
    secret = backend.read("/.env")
    denied_write = backend.write("/agents/orchestrator/graph.py", "x")

    assert memory.file_data["content"].startswith("# Bursa Analyst Agent Memory")
    assert any(
        entry["path"].endswith("/SKILL.md") or entry["is_dir"]
        for entry in skills.entries
    )
    assert secret.error
    assert denied_write.error


def test_memory_skill_backend_supports_async_deepagents_protocol() -> None:
    async def check_backend() -> None:
        backend = build_workspace_backend()

        skills = await backend.als(SKILL_PATHS[0])
        memory = await backend.aread(MEMORY_PATHS[0])
        denied_write = await backend.awrite("/agents/orchestrator/graph.py", "x")

        assert skills.entries
        assert memory.file_data["content"].startswith("# Bursa Analyst Agent Memory")
        assert denied_write.error

    asyncio.run(check_backend())


def test_research_plan_classifies_latest_results_without_forcing_all_capabilities() -> (
    None
):
    plan = build_research_plan("Summarise the latest quarterly results for Focus Point")

    assert plan.intent == ResearchIntent.LATEST_RESULTS
    assert plan.needs_filings is True
    assert plan.needs_live_news is True
    assert plan.needs_comparison is False
    assert plan.needs_subagent_debate is False
    assert plan.needs_market_data is True
    assert "latest reporting period" in plan.evidence_requirements


def test_research_plan_keeps_full_statement_requests_filing_shaped() -> None:
    plan = build_research_plan(
        "Fetch the full latest quarterly financial statements and summarise cash flow."
    )
    strength_plan = build_research_plan(
        "Is the balance sheet stronger and is debt risky?"
    )

    assert plan.intent == ResearchIntent.FILING_REVIEW
    assert plan.needs_filings is True
    assert plan.needs_market_data is False
    assert strength_plan.intent in {
        ResearchIntent.FINANCIAL_STRENGTH,
        ResearchIntent.RISK_REVIEW,
    }
    assert strength_plan.needs_market_data is True


def test_market_news_research_plan_uses_tavily_and_klse_channels() -> None:
    plan = build_research_plan(
        "What changed recently in the market news for Focus Point?"
    )

    assert plan.intent == ResearchIntent.RECENT_DEVELOPMENTS
    assert plan.needs_live_news is True
    assert plan.needs_market_data is True


def test_company_resolution_flags_ambiguous_or_unknown_identity() -> None:
    known = resolve_company_identity("Focus Point")
    ambiguous = resolve_company_identity("ABC")
    unknown = resolve_company_identity("Definitely Not A Listed Company")

    assert known.status == CompanyResolutionStatus.RESOLVED
    assert known.stock_code == "0157"
    assert ambiguous.status == CompanyResolutionStatus.AMBIGUOUS
    assert unknown.status == CompanyResolutionStatus.UNKNOWN
    assert normalize_bursa_stock_code("0157.KL") == "0157"


def test_company_resolution_uses_registry_and_returns_ambiguity_candidates() -> None:
    registry = (
        CompanyRegistryEntry("1111", "Alpha Holdings Berhad", aliases=("Alpha",)),
        CompanyRegistryEntry(
            "2222", "Alpha Technologies Berhad", aliases=("Alpha Tech",)
        ),
        CompanyRegistryEntry("3333", "Beta Manufacturing Berhad", aliases=("Beta",)),
    )

    resolved = resolve_company_identity("Beta", registry=registry)
    ambiguous = resolve_company_identity("Alpha", registry=registry)

    assert resolved.status == CompanyResolutionStatus.RESOLVED
    assert resolved.stock_code == "3333"
    assert ambiguous.status == CompanyResolutionStatus.AMBIGUOUS
    assert ambiguous.candidates == (
        "1111 - Alpha Holdings Berhad",
        "2222 - Alpha Technologies Berhad",
    )


def test_resolve_bursa_company_uses_indexed_registry(monkeypatch) -> None:
    async def fake_list_indexed_bursa_companies():
        return [
            CompanyRegistryEntry(
                stock_code="9999",
                company_name="Indexed Example Berhad",
                aliases=("Indexed Example",),
            )
        ]

    monkeypatch.setattr(
        orchestrator_graph,
        "list_indexed_bursa_companies",
        fake_list_indexed_bursa_companies,
    )

    result = asyncio.run(
        orchestrator_graph.resolve_bursa_company.ainvoke(
            {"company_query": "Indexed Example"}
        )
    )

    assert "status=resolved" in result
    assert "stock_code=9999" in result
    assert "resolver_registry=indexed_filings" in result


def test_api_rejects_known_company_stock_code_mismatch(monkeypatch) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("telemetry should not be fetched after identity mismatch")

    monkeypatch.setattr("agents.main.fetch_klse_telemetry", fail_if_called)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            execute_analysis(
                ResearchRequest(
                    stock_code="9999",
                    company_name="Focus Point Holdings Berhad",
                )
            )
        )

    assert exc_info.value.status_code == 404
    assert "Company identity mismatch" in exc_info.value.detail


def test_research_endpoint_skips_market_data_when_plan_does_not_need_it(
    monkeypatch,
) -> None:
    class FakeDesk:
        async def run_research(self, *, stock_code, company_name, question, telemetry):
            assert stock_code == "0157"
            assert company_name == "Focus Point Holdings Berhad"
            assert "financial statements" in question
            assert telemetry is None
            return CompanyResearchResponse(
                target_ticker="0157.KL",
                target_company=company_name,
                question=question,
                intent="filing_review",
                answer_markdown="Filing summary.",
                source_summary=ResearchSourceSummary(filings_used=True),
            )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("telemetry should not be fetched for filing-only research")

    monkeypatch.setattr("agents.main.fetch_klse_telemetry", fail_if_called)
    app.state.research_desk = FakeDesk()

    response = asyncio.run(
        execute_research(
            ResearchRequest(
                stock_code="0157",
                company_name="Focus Point Holdings Berhad",
                question="Fetch the full latest quarterly financial statements.",
            )
        )
    )

    assert response.intent == "filing_review"
    assert response.source_summary.market_data_used is False


def test_research_endpoint_uses_klse_for_market_news_requests(monkeypatch) -> None:
    class FakeDesk:
        async def run_research(self, *, stock_code, company_name, question, telemetry):
            assert stock_code == "0157"
            assert company_name == "Focus Point Holdings Berhad"
            assert "market news" in question
            assert telemetry is not None
            assert telemetry.current_price_myr == 0.52
            return CompanyResearchResponse(
                target_ticker="0157.KL",
                target_company=company_name,
                question=question,
                intent="recent_developments",
                answer_markdown="Market and news summary.",
                source_summary=ResearchSourceSummary(
                    live_news_used=True,
                    market_data_used=True,
                ),
            )

    monkeypatch.setattr(
        "agents.main.fetch_klse_telemetry",
        lambda stock_code: klse_market_data.LiquidityProfile(
            current_price_myr=0.52,
            rsi_14=55.0,
            adv_30d=100000,
            turnover_30d_myr=52000.0,
            liquidity_status="Illiquid/Caution",
        ),
    )
    app.state.research_desk = FakeDesk()

    response = asyncio.run(
        execute_research(
            ResearchRequest(
                stock_code="0157",
                company_name="Focus Point Holdings Berhad",
                question="What changed recently in the market news?",
            )
        )
    )

    assert response.source_summary.live_news_used is True
    assert response.source_summary.market_data_used is True


def test_structured_report_pipeline_reuses_flexible_research_path(monkeypatch) -> None:
    class FakeAgent:
        async def ainvoke(self, payload):
            raise AssertionError(
                "legacy run should call run_research instead of invoking agent directly"
            )

    class FakeStructuredLlm:
        async def ainvoke(self, prompt):
            assert "Reusable deliberation." in prompt
            return AgentAnalysisOutput(
                verdict="HOLD",
                intrinsic_low=None,
                intrinsic_high=None,
                valuation_basis="No valuation evidence supplied.",
                valuation_confidence="low",
                fundamental_findings=["Filing evidence is limited."],
                bull_arguments=[],
                bear_arguments=["Insufficient evidence."],
                risk_mitigations=["Monitor new filings."],
                executive_summary="Hold pending stronger evidence.",
            )

    async def fake_run_research(self, *, stock_code, company_name, question, telemetry):
        return CompanyResearchResponse(
            target_ticker=f"{stock_code}.KL",
            target_company=company_name,
            question=question,
            intent="broad_analysis",
            answer_markdown="Reusable deliberation.",
            source_summary=ResearchSourceSummary(),
        )

    monkeypatch.setattr(
        orchestrator_graph.BursaResearchDesk, "run_research", fake_run_research
    )
    monkeypatch.setattr(orchestrator_graph, "structured_llm", FakeStructuredLlm())

    desk = orchestrator_graph.BursaResearchDesk()
    desk.agent = FakeAgent()
    report = asyncio.run(
        desk.run(
            stock_code="0157",
            company_name="Focus Point Holdings Berhad",
            telemetry=orchestrator_graph.LiquidityProfile(
                current_price_myr=1.0,
                rsi_14=50.0,
                adv_30d=1000,
                turnover_30d_myr=1000.0,
                liquidity_status="Illiquid/Caution",
            ),
        )
    )

    assert report.verdict == "HOLD"
    assert report.executive_summary == "Hold pending stronger evidence."


def test_filing_context_adds_period_basis_hints_for_cumulative_reports() -> None:
    context = orchestrator_graph._format_filing_context(
        [
            {
                "section": "General Notes",
                "quarter": "Q2 2026",
                "quarter_ended": "2026-06-30",
                "similarity": 1.0,
                "chunk": "CONDENSED CONSOLIDATED STATEMENTS OF CASH FLOWS For the 6 months ended 30 June 2026",
            },
            {
                "section": "Balance Sheet - Financial Position",
                "quarter": "Q2 2026",
                "quarter_ended": "2026-06-30",
                "similarity": 1.0,
                "chunk": "Statement of financial position TOTAL ASSETS TOTAL LIABILITIES",
            },
        ]
    )

    assert "Period basis hint:** cumulative_period" in context
    assert "Period basis hint:** point_in_time" in context
    assert "Quarter ended:** 2026-06-30" in context


def test_live_news_availability_detects_failed_or_empty_searches() -> None:
    assert (
        orchestrator_graph._live_news_has_results(
            "Error fetching live news intelligence: All connection attempts failed"
        )
        is False
    )
    assert (
        orchestrator_graph._live_news_has_results('{"results": [{"title": "Result"}]}')
        is True
    )
    assert orchestrator_graph._live_news_has_results('{"results": []}') is False


def test_structured_report_fallback_is_conservative() -> None:
    analysis = orchestrator_graph._fallback_analysis_from_deliberation(
        "The evidence is limited. Verdict: HOLD. Target range unavailable."
    )

    assert analysis.verdict == "HOLD"
    assert analysis.intrinsic_low is None
    assert analysis.intrinsic_high is None
    assert analysis.valuation_confidence == "low"
    assert "Structured extraction failed" in analysis.valuation_basis
    assert "unvalidated deliberation" in analysis.executive_summary


def test_rag_tool_uses_adaptive_queries_and_preserves_chunks(monkeypatch) -> None:
    calls = []
    long_chunk = "Segmental revenue " + ("detail " * 220)

    async def fake_search_bursa_notes(stock_code, query, limit):
        calls.append((stock_code, query, limit))
        if len(calls) == 1:
            return [
                {
                    "section": "General Notes",
                    "quarter": "Q2 2026",
                    "quarter_ended": "2026-06-30",
                    "similarity": 0.5,
                    "chunk": long_chunk,
                }
            ]
        return [
            {
                "section": "Part B - Dividends",
                "quarter": "Q2 2026",
                "quarter_ended": "2026-06-30",
                "similarity": 0.4,
                "chunk": "Second interim dividend details.",
            }
        ]

    monkeypatch.setattr(
        orchestrator_graph, "search_bursa_notes", fake_search_bursa_notes
    )

    payload = asyncio.run(
        orchestrator_graph.query_bursa_quarterly_filings.ainvoke(
            {"stock_code": "0157", "query": "segment revenue"}
        )
    )
    data = json.loads(payload)

    assert len(calls) == len(
        orchestrator_graph._select_filing_queries("segment revenue")
    )
    assert data["period_warning"]
    assert data["coverage"]["queries_run"] == len(calls)
    assert (
        data["coverage"]["unique_chunks_returned"] <= orchestrator_graph.MAX_RAG_CHUNKS
    )
    assert data["results"][0]["quarter_ended"] == "2026-06-30"
    assert data["results"][0]["chunk"] == " ".join(long_chunk.split())
    assert data["results_by_query"][0]["chunk_ids"]
    assert "chunk" not in data["results_by_query"][0]
    assert len(data["results"][0]["chunk"]) > 1200


def test_adaptive_filing_queries_expand_for_financial_strength() -> None:
    narrow = orchestrator_graph._select_filing_queries("segment revenue")
    strength = orchestrator_graph._select_filing_queries(
        "Is the balance sheet stronger and is debt risky?"
    )

    assert len(narrow) < len(strength)
    assert any("borrowings" in query for query in strength)
    assert any("cash flow" in query for query in strength)


def test_committee_briefing_format_matches_preferred_result_shape() -> None:
    report_format = committee_briefing_format()
    prompt = report_format.as_prompt()
    section_titles = [section.title for section in report_format.sections]

    assert section_titles == [
        "Executive Summary & Core Identity",
        "Segmental Revenue & Profitability Breakdown",
        "Balance Sheet & Cash Flow Health",
        "Bull vs. Bear Debate Matrix",
        "Committee Verdict & Action Plan",
    ]
    assert (
        "Target range unavailable due to insufficient quantitative evidence" in prompt
    )
    assert report_format.unsupported_value_label == "Unavailable in supplied evidence"
    assert "Do not include columns named Source" in prompt
    assert "raw hashes" in prompt
    assert "Source, and Comment" not in prompt
