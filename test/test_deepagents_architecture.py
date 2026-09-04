import asyncio
import json
from datetime import date

from src import studio_graph
from src.schemas.report import (
    AgentAnalysisOutput,
    EvidenceItem,
    FundamentalFindings,
    ValidationReport,
    committee_briefing_format,
)
from src.tools import bursa_rag, tavily_search


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

    payload = asyncio.run(tavily_search.search_bursa_intelligence("0157", "Focus Point"))
    data = json.loads(payload)

    assert data["stock_code"] == "0157"
    assert len(data["results"]) == 1
    assert len(data["results"][0]["title"]) <= 180
    assert len(data["results"][0]["snippet"]) <= 700


def test_balance_sheet_chunks_are_classified_as_financial_position() -> None:
    section = bursa_rag.classify_section(
        "Condensed consolidated statements of financial position TOTAL ASSETS "
        "TOTAL EQUITY AND LIABILITIES Lease liabilities"
    )

    assert section == "Balance Sheet - Financial Position"


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
                    "section_category": "Balance Sheet - Financial Position",
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

    assert len(fake_connection.calls) == 2
    assert any("TOTAL ASSETS" in result["chunk"] for result in results)
    assert results[-1]["section"] == "Balance Sheet - Financial Position"


def test_studio_graph_prompts_keep_filing_tool_and_evidence_discipline() -> None:
    main_tool_names = {tool.name for tool in studio_graph.root_tools}

    assert "query_bursa_quarterly_filings" in main_tool_names
    assert "Default to HOLD when evidence is insufficient" in studio_graph.SUPERVISOR_SYSTEM_PROMPT
    assert "Executive Summary & Core Identity" in studio_graph.SUPERVISOR_SYSTEM_PROMPT
    assert "Bull vs. Bear Debate Matrix" in studio_graph.SUPERVISOR_SYSTEM_PROMPT
    assert "Unavailable in supplied evidence" in studio_graph.SUPERVISOR_SYSTEM_PROMPT
    assert "raw source IDs" in studio_graph.SUPERVISOR_SYSTEM_PROMPT
    assert "source-clutter issues" in studio_graph.SUPERVISOR_SYSTEM_PROMPT
    assert "Do not calculate QoQ growth from Q1 versus Q2" in studio_graph.SUPERVISOR_SYSTEM_PROMPT
    assert "malformed JSON" in studio_graph.SUPERVISOR_SYSTEM_PROMPT
    assert "For every exact number" in studio_graph.fundamental_analyst["system_prompt"]
    assert "period_basis" in studio_graph.fundamental_analyst["system_prompt"]
    assert "exactly these top-level keys" in studio_graph.fundamental_analyst["system_prompt"]
    assert "unsupported_assumptions" in studio_graph.market_debater["system_prompt"]
    assert "mixes reporting periods" in studio_graph.evidence_validator["system_prompt"]


def test_rag_tool_uses_supplemental_queries_and_preserves_chunks(monkeypatch) -> None:
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

    monkeypatch.setattr(studio_graph, "search_bursa_notes", fake_search_bursa_notes)

    payload = asyncio.run(
        studio_graph.query_bursa_quarterly_filings.ainvoke(
            {"stock_code": "0157", "query": "segment revenue"}
        )
    )
    data = json.loads(payload)

    assert len(calls) == 1 + len(studio_graph.RAG_SUPPLEMENTAL_QUERIES)
    assert data["period_warning"]
    assert data["coverage"]["queries_run"] == len(calls)
    assert data["coverage"]["unique_chunks_returned"] <= studio_graph.MAX_RAG_CHUNKS
    assert data["results"][0]["quarter_ended"] == "2026-06-30"
    assert data["results"][0]["chunk"] == " ".join(long_chunk.split())
    assert data["results_by_query"][0]["chunk_ids"]
    assert "chunk" not in data["results_by_query"][0]
    assert len(data["results"][0]["chunk"]) > 1200


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
    assert "Target range unavailable due to insufficient quantitative evidence" in prompt
    assert report_format.unsupported_value_label == "Unavailable in supplied evidence"
    assert "Do not include columns named Source" in prompt
    assert "raw hashes" in prompt
    assert "Source, and Comment" not in prompt
