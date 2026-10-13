"""
Institutional Financial Tools for DeepAgent / LangChain / LangGraph.
Wires native market data, portfolio analytics, financial news, and macro indicators.
"""

from __future__ import annotations
import os
import requests
from urllib.parse import urlsplit, urlencode
import json
import logging
import math
from typing import Any, List, Optional, Literal
from datetime import datetime, timezone, timedelta
from orchestrator.freshness import now_utc

from langchain_core.tools import BaseTool, tool
from langchain.tools import ToolRuntime
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

    if hasattr(fmp_client, "FMPClient"):
        _fmp_client = fmp_client.FMPClient()
    elif hasattr(fmp_client, "client"):
        _fmp_client = fmp_client.client
    else:
        _fmp_client = fmp_client
except Exception as e:
    logger.warning("FMP client unavailable: %s", sanitize_error(e))
    _fmp_client = None


def _to_json(data: Any) -> str:
    """Safe serializer ensuring clean JSON strings for LLM context."""
    return json.dumps(data, indent=2, ensure_ascii=False)


# -----------------------------------------------------------------------------
# Tool 1: Real-Time Market Data & Valuations
# -----------------------------------------------------------------------------


