from research_agent.prompts import FINANCIAL_ANALYST_SYSTEM_PROMPT


def test_main_prompt_is_compact_and_clean_report_oriented():
    prompt = FINANCIAL_ANALYST_SYSTEM_PROMPT

    assert len(prompt) < 1200
    assert "Target Price" in prompt
    assert "No source tables" in prompt
    assert "financial_statement_table_markdown" in prompt
    assert "latest valuation-ratio table" in prompt
    assert "missing_quarter_retry" in prompt
    assert "Use exactly the four section headings" in prompt
    assert "## 1. Executive Summary" in prompt
    assert "Do not create ROE" in prompt
    assert "This research is for education only and is not personal financial advice." in prompt
