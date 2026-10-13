"""Compose the current provider tools; implementations live by capability."""
from .market import _make_market_data_tool, _make_company_search_tool
from .portfolio import _make_portfolio_tool
from .news import _make_news_tool
from .economics import _make_economics_tool

def build_financial_tools():
    """Build and return all active institutional finance tools for the agent."""
    return [
        _make_company_search_tool(),
        _make_market_data_tool(),
        _make_portfolio_tool(),
        _make_news_tool(),
        _make_economics_tool(),
    ]
