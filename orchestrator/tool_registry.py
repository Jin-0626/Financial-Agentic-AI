"""Shared tool names, roles and evidence classification."""

# Public market/macro retrieval may be reused and forwarded across specialists.
# Portfolio, script, and native calculation inputs may contain private data.
PUBLIC_PROVIDER_TOOLS = frozenset(
    {"company_search", "market_data", "financial_news", "economics_data"}
)

ROLE_TOOLS = {
    "orchestrator": {
        "company_search",
        "market_data",
        "financial_news",
        "economics_data",
        "portfolio_analytics",
        "task",
        "write_todos",
        "run_script",
    },
    "research": {
        "write_todos",
        "company_search",
        "market_data",
        "financial_news",
        "economics_data",
        "run_script",
    },
    "valuation": {
        "write_todos",
        "company_search",
        "market_data",
        "run_script",
        "extract_financial_ratios",
        "compute_discounted_cash_flow",
        "register_forecast",
    },
    "risk": {
        "write_todos",
        "company_search",
        "market_data",
        "run_script",
        "portfolio_analytics",
        "calculate_historical_var",
        "run_monte_carlo_simulation",
    },
    "synthesis": {"write_todos"},
}
SPECIALIST_ROLES = {
    "research": "research",
    "macro-economist": "research",
    "general-purpose": "research",
    "data-analyst": "valuation",
    "trading": "risk",
    "risk-analyzer": "risk",
    "portfolio-optimizer": "risk",
    "backtester": "risk",
    "reporter": "synthesis",
}

FINANCIAL_TOOLS = frozenset().union(
    *(names - {"task", "write_todos"} for names in ROLE_TOOLS.values())
)
NATIVE_TOOLS = frozenset(
    {
        "compute_discounted_cash_flow",
        "calculate_historical_var",
        "extract_financial_ratios",
        "run_monte_carlo_simulation",
        "register_forecast",
    }
)
