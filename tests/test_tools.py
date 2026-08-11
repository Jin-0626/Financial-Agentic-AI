from research_agent.market_data import compact_balance_sheet, compact_cash_flow
from research_agent.schemas import DCFValuationResult, StockInfoResult
from research_agent.search import build_fast_market_context, build_market_news_context, build_missing_quarter_search
from research_agent.tools import (
    build_bursa_research_snapshot,
    calculate_dcf_valuation,
    fetch_bursa_quarterly_reports,
    fetch_bursa_stock_data,
    resolve_bursa_ticker,
    search_market_context,
    search_official_bursa_filings,
)


def test_resolve_bursa_ticker_numeric_code():
    assert resolve_bursa_ticker("5275") == "5275.KL"


def test_resolve_bursa_ticker_preserves_kl_suffix():
    assert resolve_bursa_ticker("5275.KL") == "5275.KL"


def test_dcf_valuation_handles_invalid_price():
    result = calculate_dcf_valuation.invoke({"current_price": None, "pe_ratio": 12})
    parsed = DCFValuationResult.model_validate(result)

    assert parsed.status == "FAILED"
    assert parsed.error


def test_dcf_valuation_handles_missing_pe_with_warning():
    result = calculate_dcf_valuation.invoke({"current_price": 1.25, "pe_ratio": None})
    parsed = DCFValuationResult.model_validate(result)

    assert parsed.status == "SUCCESS"
    assert parsed.pe_ratio_substituted is True
    assert parsed.estimated_fair_value_myr is not None
    assert parsed.warnings


def test_stock_tool_failure_shape(monkeypatch):
    class FailingTicker:
        @property
        def info(self):
            raise RuntimeError("network unavailable")

    monkeypatch.setattr("research_agent.tools.yf.Ticker", lambda symbol: FailingTicker())
    result = fetch_bursa_stock_data.invoke({"ticker_code_or_name": "5275"})
    parsed = StockInfoResult.model_validate(result)

    assert parsed.symbol == "5275.KL"
    assert parsed.status == "FAILED"
    assert parsed.warnings


def test_research_snapshot_handles_failed_market_data(monkeypatch):
    monkeypatch.setattr(
        "research_agent.tools.YFinanceUtils.get_stock_info",
        staticmethod(
            lambda symbol: {
                "status": "FAILED",
                "symbol": "5275.KL",
                "company_name": "5275",
                "current_price": None,
                "pe_ratio": None,
                "warnings": ["network unavailable"],
            }
        ),
    )
    monkeypatch.setattr(
        "research_agent.tools.YFinanceUtils.get_quarterly_reports",
        staticmethod(lambda symbol: {"symbol": "5275.KL", "company_name": "5275", "quarterly_financials": {}}),
    )
    monkeypatch.setattr("research_agent.tools._compact_balance_sheet", lambda stock: {})
    monkeypatch.setattr("research_agent.tools._compact_cash_flow", lambda stock: {})
    monkeypatch.setattr(
        "research_agent.tools.build_fast_market_context",
        lambda company_name, ticker, sector="", industry="": {
            "market_news": {"query": "", "items": []},
            "macro_news": {"query": "", "items": []},
            "micro_industry_news": {"query": "", "items": []},
            "competitor_analysis": {"query": "", "items": []},
        },
    )
    monkeypatch.setattr(
        "research_agent.tools.build_missing_quarter_search",
        lambda company_name, ticker, known_periods: {
            "status": "FAILED",
            "missing_count": 4,
            "known_periods": known_periods,
            "sources": [],
            "warnings": ["searched"],
        },
    )

    result = build_bursa_research_snapshot.invoke({"ticker_code_or_name": "5275"})

    assert result["stock"]["status"] == "FAILED"
    assert result["data_quality"]["status"] == "partial"
    assert result["valuation"]["target_price_myr"] is None
    assert result["forecast_explanation"]["target_price_myr"] is None
    assert result["missing_quarter_retry"]["warnings"] == ["searched"]


def test_official_search_result_is_token_compact(monkeypatch):
    from research_agent.schemas import SourceRecord
    from research_agent.tools import _cached_official_bursa_filings

    def fake_search(query: str, *, max_results: int = 5):
        return [
            SourceRecord(
                title=f"Result {idx}",
                url=f"https://example.com/{idx}",
                provider="test",
                snippet="x" * 260,
            )
            for idx in range(4)
        ]

    _cached_official_bursa_filings.cache_clear()
    monkeypatch.setattr("research_agent.search.search_with_tavily", fake_search)

    result = search_official_bursa_filings.invoke({"company_name_or_ticker": "5275"})

    assert len(result["sources"]) <= 4
    assert len(str(result)) < 2000


def test_official_search_filters_irrelevant_redirects(monkeypatch):
    from research_agent.schemas import SourceRecord
    from research_agent.tools import _cached_official_bursa_filings

    def fake_search(query: str, *, max_results: int = 5):
        return [
            SourceRecord(
                title="Escherichia coli O157:H7",
                url="/goto?url=irrelevant",
                provider="test",
                snippet="Foodborne pathogen.",
            ),
            SourceRecord(
                title="Focus Point Holdings Berhad quarterly report",
                url="https://example.com/focus-point-quarterly.pdf",
                provider="test",
                snippet="Focus Point revenue and profit summary.",
            ),
        ]

    _cached_official_bursa_filings.cache_clear()
    monkeypatch.setattr("research_agent.search.search_with_tavily", fake_search)

    result = search_official_bursa_filings.invoke({"company_name_or_ticker": "Focus Point Holdings Berhad"})

    assert len(result["sources"]) == 1
    assert "Focus Point" in result["sources"][0]["title"]


def test_official_search_tolerates_common_argument_typo(monkeypatch):
    monkeypatch.setattr(
        "research_agent.tools._cached_official_bursa_filings",
        lambda query: {"query": query, "status": "SUCCESS", "sources": []},
    )

    result = search_official_bursa_filings.invoke({"company_name_or_tticker": "Focus Point Holdings Berhad"})

    assert result["query"] == "Focus Point Holdings Berhad"


def test_research_snapshot_combines_core_tools(monkeypatch):
    monkeypatch.setattr(
        "research_agent.tools.YFinanceUtils.get_stock_info",
        staticmethod(
            lambda symbol: {
                "status": "SUCCESS",
                "symbol": "5275.KL",
                "company_name": "Test Bhd",
                "currency": "MYR",
                "current_price": 1.0,
                "pe_ratio": 10.0,
                "price_to_book": 1.2,
                "enterprise_to_ebitda": 8.5,
                "sector": "Consumer",
                "industry": "Retail",
                "summary": "Runs a Malaysian retail store network.",
            }
        ),
    )
    monkeypatch.setattr(
        "research_agent.tools.YFinanceUtils.get_quarterly_reports",
        staticmethod(
            lambda symbol: {
                "symbol": "5275.KL",
                "company_name": "Test Bhd",
                "quarterly_financials": {"2026-03-31": {"revenue_myr_m": 100.0, "diluted_eps": 0.02}},
            }
        ),
    )
    monkeypatch.setattr("research_agent.tools._search_with_tavily", lambda query, *, max_results=3: [])
    monkeypatch.setattr(
        "research_agent.search.search_with_tavily",
        lambda query, *, max_results=3: [],
    )
    monkeypatch.setattr(
        "research_agent.tools.build_missing_quarter_search",
        lambda company_name, ticker, known_periods: {
            "status": "FAILED",
            "missing_count": max(0, 4 - len(known_periods)),
            "known_periods": known_periods,
            "sources": [],
            "warnings": ["retry attempted"],
        },
    )
    monkeypatch.setattr(
        "research_agent.tools._compact_balance_sheet",
        lambda stock: {"2026-03-31": {"total_assets_myr_m": 500.0, "total_debt_myr_m": 120.0}},
    )
    monkeypatch.setattr(
        "research_agent.tools._compact_cash_flow",
        lambda stock: {"2026-03-31": {"operating_cash_flow_myr_m": 30.0, "free_cash_flow_myr_m": 20.0}},
    )

    result = build_bursa_research_snapshot.invoke({"ticker_code_or_name": "5275"})

    assert result["stock"]["symbol"] == "5275.KL"
    assert result["valuation"]["target_price_myr"] is not None
    assert "quarters" in result
    assert result["data_quality"]["status"] == "complete"
    assert result["income_statement"] == result["quarterly_financial_summary"]
    assert result["balance_sheet"]["2026-03-31"]["total_assets_myr_m"] == 500.0
    assert result["cash_flow"]["2026-03-31"]["free_cash_flow_myr_m"] == 20.0
    assert result["valuation_ratios"]["price_to_book"] == 1.2
    assert result["valuation_ratios"]["ev_to_ebitda"] == 8.5
    assert "financial_statement_table_markdown" in result
    assert "| Metric (Last 4Q) |" in result["financial_statement_table_markdown"]
    assert "| Free Cash Flow (RMm) | 20 | N/A | N/A | N/A |" in result["financial_statement_table_markdown"]
    assert "**Latest Valuation Ratios**" in result["financial_statement_table_markdown"]
    assert "| Metric | Latest |" in result["financial_statement_table_markdown"]
    assert "| P/E (trailing) | 10x |" in result["financial_statement_table_markdown"]
    assert "| EV/EBITDA" not in result["financial_statement_table_markdown"]
    assert "| P/E (trailing) | 10 | N/A | N/A | N/A |" not in result["financial_statement_table_markdown"]
    assert "Q-3" in result["financial_statement_table_markdown"]
    assert result["missing_quarter_retry"]["missing_count"] == 3
    assert result["sector_insight"]["sector"] == "Consumer"
    assert "pricing and procurement discipline" in result["sector_insight"]["demand_drivers"]
    assert result["forecast_explanation"]["target_price_myr"] is not None
    assert result["forecast_explanation"]["base_eps_myr"] is not None
    assert set(result["market_context"]) == {
        "market_news",
        "macro_news",
        "micro_industry_news",
        "competitor_analysis",
    }


def test_research_snapshot_retries_when_balance_or_cash_flow_lacks_four_quarters(monkeypatch):
    captured_known_periods = []

    monkeypatch.setattr(
        "research_agent.tools.YFinanceUtils.get_stock_info",
        staticmethod(
            lambda symbol: {
                "status": "SUCCESS",
                "symbol": "0157.KL",
                "company_name": "Focus Point Holdings Berhad",
                "currency": "MYR",
                "current_price": 0.51,
                "pe_ratio": 8.5,
            }
        ),
    )
    monkeypatch.setattr(
        "research_agent.tools.YFinanceUtils.get_quarterly_reports",
        staticmethod(
            lambda symbol: {
                "symbol": "0157.KL",
                "company_name": "Focus Point Holdings Berhad",
                "quarterly_financials": {
                    "2026-03-31": {"revenue_myr_m": 77.27},
                    "2025-12-31": {"revenue_myr_m": 91.17},
                    "2025-09-30": {"revenue_myr_m": 74.51},
                    "2025-06-30": {"revenue_myr_m": 72.78},
                },
            }
        ),
    )
    monkeypatch.setattr(
        "research_agent.tools._compact_balance_sheet",
        lambda stock: {
            "2026-03-31": {"cash_myr_m": 22.99},
            "2025-12-31": {"cash_myr_m": 49.18},
            "2025-09-30": {"cash_myr_m": 38.17},
        },
    )
    monkeypatch.setattr(
        "research_agent.tools._compact_cash_flow",
        lambda stock: {
            "2026-03-31": {"operating_cash_flow_myr_m": 16.23},
            "2025-12-31": {"operating_cash_flow_myr_m": 31.3},
            "2025-09-30": {"operating_cash_flow_myr_m": 16.09},
        },
    )
    monkeypatch.setattr(
        "research_agent.tools.build_missing_quarter_search",
        lambda company_name, ticker, known_periods: (
            captured_known_periods.extend(known_periods)
            or {
                "status": "FAILED",
                "missing_count": max(0, 4 - len(known_periods)),
                "known_periods": known_periods,
                "sources": [],
                "warnings": ["retry attempted"],
            }
        ),
    )
    monkeypatch.setattr(
        "research_agent.tools.build_fast_market_context",
        lambda company_name, ticker, sector="", industry="": {
            "market_news": {"query": "", "items": []},
            "macro_news": {"query": "", "items": []},
            "micro_industry_news": {"query": "", "items": []},
            "competitor_analysis": {"query": "", "items": []},
        },
    )

    result = build_bursa_research_snapshot.invoke({"ticker_code_or_name": "0157.KL"})

    assert captured_known_periods == ["2026-03-31", "2025-12-31", "2025-09-30"]
    assert result["missing_quarter_retry"]["missing_count"] == 1
    assert "| Revenue (RMm) | 77.27 | 91.17 | 74.51 | 72.78 |" in result["financial_statement_table_markdown"]
    assert "| Cash (RMm) | 22.99 | 49.18 | 38.17 | N/A |" in result["financial_statement_table_markdown"]
    assert "**Latest Valuation Ratios**" in result["financial_statement_table_markdown"]


def test_quarterly_financials_keep_negative_cash_flow_values(monkeypatch):
    import pandas as pd

    class StatementTicker:
        @property
        def info(self):
            return {"longName": "Test Bhd"}

        @property
        def quarterly_financials(self):
            return pd.DataFrame(
                {"2026-03-31": {"Total Revenue": 100_000_000, "Net Income": -5_000_000, "Diluted EPS": -0.01}}
            )

    monkeypatch.setattr("research_agent.tools.yf.Ticker", lambda symbol: StatementTicker())

    result = fetch_bursa_quarterly_reports.invoke({"ticker_code_or_name": "5275"})

    assert result["quarterly_financials"]["2026-03-31"]["net_income_myr_m"] == -5.0
    assert result["quarterly_financials"]["2026-03-31"]["diluted_eps"] == -0.01


def test_quarterly_financials_keep_last_four_quarters(monkeypatch):
    import pandas as pd

    class StatementTicker:
        @property
        def info(self):
            return {"longName": "Test Bhd"}

        @property
        def quarterly_financials(self):
            return pd.DataFrame(
                {
                    "2026-06-30": {"Total Revenue": 400_000_000, "Net Income": 40_000_000},
                    "2026-03-31": {"Total Revenue": 300_000_000, "Net Income": 30_000_000},
                    "2025-12-31": {"Total Revenue": 200_000_000, "Net Income": 20_000_000},
                    "2025-09-30": {"Total Revenue": 100_000_000, "Net Income": 10_000_000},
                    "2025-06-30": {"Total Revenue": 50_000_000, "Net Income": 5_000_000},
                }
            )

    monkeypatch.setattr("research_agent.tools.yf.Ticker", lambda symbol: StatementTicker())

    result = fetch_bursa_quarterly_reports.invoke({"ticker_code_or_name": "5275"})

    assert list(result["quarterly_financials"]) == ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"]
    assert "2025-06-30" not in result["quarterly_financials"]


def test_balance_sheet_keeps_last_four_quarters():
    import pandas as pd

    class StatementTicker:
        @property
        def quarterly_balance_sheet(self):
            return pd.DataFrame(
                {
                    "2026-03-31": {"Cash And Cash Equivalents": 40_000_000, "Total Assets": 400_000_000},
                    "2025-12-31": {"Cash And Cash Equivalents": 30_000_000, "Total Assets": 300_000_000},
                    "2025-09-30": {"Cash And Cash Equivalents": 20_000_000, "Total Assets": 200_000_000},
                    "2025-06-30": {"Cash And Cash Equivalents": 10_000_000, "Total Assets": 100_000_000},
                    "2025-03-31": {"Cash And Cash Equivalents": 5_000_000, "Total Assets": 50_000_000},
                }
            )

    result = compact_balance_sheet(StatementTicker())

    assert list(result) == ["2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30"]
    assert result["2025-06-30"]["cash_myr_m"] == 10.0
    assert "2025-03-31" not in result


def test_cash_flow_keeps_last_four_quarters():
    import pandas as pd

    class StatementTicker:
        @property
        def quarterly_cashflow(self):
            return pd.DataFrame(
                {
                    "2026-03-31": {"Operating Cash Flow": 40_000_000, "Free Cash Flow": 35_000_000},
                    "2025-12-31": {"Operating Cash Flow": 30_000_000, "Free Cash Flow": 25_000_000},
                    "2025-09-30": {"Operating Cash Flow": 20_000_000, "Free Cash Flow": 15_000_000},
                    "2025-06-30": {"Operating Cash Flow": 10_000_000, "Free Cash Flow": 5_000_000},
                    "2025-03-31": {"Operating Cash Flow": 4_000_000, "Free Cash Flow": 3_000_000},
                }
            )

    result = compact_cash_flow(StatementTicker())

    assert list(result) == ["2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30"]
    assert result["2025-06-30"]["free_cash_flow_myr_m"] == 5.0
    assert "2025-03-31" not in result


def test_market_news_context_groups_web_research(monkeypatch):
    from research_agent.schemas import SourceRecord

    def fake_search(query: str, *, max_results: int = 3):
        return [
            SourceRecord(
                title=f"Headline for {query}",
                url=f"https://example.com/{max_results}",
                provider="test",
                snippet="Relevant market signal that may affect the company.",
            )
        ]

    monkeypatch.setattr("research_agent.search.search_with_tavily", fake_search)

    context = build_market_news_context(
        company_name="Test Bhd",
        ticker="5275.KL",
        sector="Consumer Defensive",
        industry="Food Retail",
    )

    assert context["market_news"]["items"]
    assert context["macro_news"]["items"]
    assert context["micro_industry_news"]["items"]
    assert context["competitor_analysis"]["items"]
    assert "competitors Bursa Malaysia" in context["competitor_analysis"]["query"]


def test_fast_market_context_uses_one_compact_search(monkeypatch):
    from research_agent.schemas import SourceRecord

    calls = []

    def fake_search(query: str, *, max_results: int = 3):
        calls.append((query, max_results))
        return [
            SourceRecord(title="Market item", url="https://example.com/1", provider="test", snippet="Market signal"),
            SourceRecord(title="Macro item", url="https://example.com/2", provider="test", snippet="Macro signal"),
        ]

    monkeypatch.setattr("research_agent.search.search_with_tavily", fake_search)

    context = build_fast_market_context("Test Bhd", "5275.KL", "Consumer", "Retail")

    assert len(calls) == 1
    assert calls[0][1] == 2
    assert len(context["market_news"]["items"]) == 1
    assert len(context["macro_news"]["items"]) == 1


def test_fast_market_context_survives_tavily_failure(monkeypatch):
    def failing_search(query: str, *, max_results: int = 3):
        raise RuntimeError("search backend down")

    monkeypatch.setattr("research_agent.search.search_with_tavily", failing_search)

    context = build_fast_market_context("Test Bhd", "5275.KL", "Consumer", "Retail")

    assert context["market_news"]["items"] == []
    assert context["macro_news"]["items"] == []


def test_missing_quarter_search_retries_with_official_first_queries(monkeypatch):
    from research_agent.schemas import SourceRecord

    calls = []

    def fake_search(query: str, *, max_results: int = 3):
        calls.append((query, max_results))
        return [
            SourceRecord(
                title="Mynews Holdings quarterly report",
                url="https://www.bursamalaysia.com/quarterly-report",
                provider="test",
                snippet="Quarterly report includes revenue and profit.",
            )
        ]

    monkeypatch.setattr("research_agent.search.search_with_tavily", fake_search)

    result = build_missing_quarter_search("Mynews Holdings Berhad", "5275.KL", ["2026-04-30", "2026-01-31"])

    assert result["status"] == "SUCCESS"
    assert result["missing_count"] == 2
    assert calls[0][0].startswith("site:bursamalaysia.com 5275")
    assert len(result["sources"]) == 1


def test_missing_quarter_search_skips_when_four_quarters_present(monkeypatch):
    calls = []
    monkeypatch.setattr("research_agent.search.search_with_tavily", lambda query, *, max_results=3: calls.append(query))

    result = build_missing_quarter_search("Test Bhd", "5275.KL", ["Q1", "Q2", "Q3", "Q4"])

    assert result["status"] == "not_needed"
    assert calls == []


def test_market_context_tool_is_callable(monkeypatch):
    monkeypatch.setattr(
        "research_agent.tools.build_market_news_context",
        lambda company_name, ticker, sector="", industry="": {
            "market_news": {"query": company_name, "items": []},
            "macro_news": {"query": sector, "items": []},
            "micro_industry_news": {"query": industry, "items": []},
            "competitor_analysis": {"query": ticker, "items": []},
        },
    )

    result = search_market_context.invoke(
        {
            "company_name": "Test Bhd",
            "ticker": "5275.KL",
            "sector": "Consumer",
            "industry": "Retail",
        }
    )

    assert result["competitor_analysis"]["query"] == "5275.KL"
