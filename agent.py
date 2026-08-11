from deepagents import create_deep_agent
from deepagents.profiles import GeneralPurposeSubagentProfile, HarnessProfile, register_harness_profile

from research_agent import (
    FINANCIAL_ANALYST_SYSTEM_PROMPT,
    build_bursa_research_snapshot,
    calculate_dcf_valuation,
    fetch_bursa_quarterly_reports,
    fetch_bursa_stock_data,
    search_market_context,
    search_official_bursa_filings,
)
from utils import get_ollama_cloud_model

LEAN_ANALYST_PROFILE = HarnessProfile(
    excluded_tools=frozenset(
        {
            "ls",
            "read_file",
            "write_file",
            "edit_file",
            "glob",
            "grep",
            "execute",
            
        }
    ),
    general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=True),
)
register_harness_profile("ollama", LEAN_ANALYST_PROFILE)

tools = [
    build_bursa_research_snapshot,
    fetch_bursa_stock_data,
    fetch_bursa_quarterly_reports,
    search_official_bursa_filings,
    search_market_context,
    calculate_dcf_valuation,
]

graph = create_deep_agent(
    model=get_ollama_cloud_model(),
    tools=tools,
    system_prompt=FINANCIAL_ANALYST_SYSTEM_PROMPT,
)
