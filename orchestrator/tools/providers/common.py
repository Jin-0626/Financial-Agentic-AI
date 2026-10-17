"""
Institutional Financial Tools for DeepAgent / LangChain / LangGraph.
Wires native market data, portfolio analytics, financial news, and macro indicators.
"""

from __future__ import annotations
import json
import logging
from typing import Any

from app.infrastructure.diagnostics import sanitize_error


logger = logging.getLogger(__name__)

_dependency_errors: dict[str, str] = {}

# Map actual script filenames from scripts/ directory
try:
    from scripts.data_sources import yf_data as stock_data
except Exception as e:
    logger.error(
        "Failed to from scripts.data_sources import yf_data as stock_data: %s", sanitize_error(e)
    )
    _dependency_errors["yfinance"] = sanitize_error(e)
    stock_data = None

try:
    from scripts.data_sources import yh_market as yahoo_market_data
except Exception as e:
    logger.error(
        "Failed to from scripts.data_sources import yh_market as yahoo_market_data: %s",
        sanitize_error(e),
    )
    _dependency_errors["yahoo"] = sanitize_error(e)
    yahoo_market_data = None

_fmp_client = None
try:
    from scripts.data_sources import fmp_client

    _fmp_client = fmp_client.FMPClient()
except Exception as e:
    logger.warning("FMP client unavailable: %s", sanitize_error(e))
    _fmp_client = None


def _to_json(data: Any) -> str:
    """Safe serializer ensuring clean JSON strings for LLM context."""
    return json.dumps(data, indent=2, ensure_ascii=False)
