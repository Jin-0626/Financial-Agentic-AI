from pathlib import Path


def test_streamlit_app_source_is_import_guard_friendly():
    app_source = Path("app.py").read_text(encoding="utf-8")

    assert "st.set_page_config" in app_source
    assert "cached_stock_info" in app_source
    assert "run_agent_report" in app_source
    assert "Stop run" in app_source
    assert "cancelled_run_ids" in app_source
    assert "First call build_bursa_research_snapshot once" in app_source
    assert "financial_statement_table_markdown exactly" in app_source
    assert "latest valuation-ratio table" in app_source
    assert "missing_quarter_retry" in app_source
    assert "Use exactly these four numbered sections" in app_source
    assert "500-700 words" in app_source
    assert "Download Markdown Report" in app_source
