import agent


def test_deepagent_graph_is_exported():
    assert agent.graph is not None


def test_required_tools_are_registered():
    tool_names = {tool.name for tool in agent.tools}

    assert "build_bursa_research_snapshot" in tool_names
    assert "fetch_bursa_stock_data" in tool_names
    assert "fetch_bursa_quarterly_reports" in tool_names
    assert "calculate_dcf_valuation" in tool_names
    assert "search_official_bursa_filings" in tool_names
    assert "search_market_context" in tool_names
    assert "normalize_bursa_ticker" not in tool_names
    assert "search_bursa_stock" not in tool_names
    assert "search_bursa_news" not in tool_names
    assert "calculate_valuation_multiples" not in tool_names
    assert "format_equity_report" not in tool_names
