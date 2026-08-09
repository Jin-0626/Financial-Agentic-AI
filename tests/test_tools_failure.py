import os
from unittest.mock import patch

import pandas as pd
import pytest
from tools import YFinanceUtils, calculate_dcf_val

pytestmark = pytest.mark.failure


def test_quarterly_reports_fallback_failure():
  """Failure Case: Test behavior when yfinance is empty AND Tavily API Key is missing."""
  with patch("yfinance.Ticker") as mock_ticker:
    mock_instance = mock_ticker.return_value
    mock_instance.quarterly_financials = None
    mock_instance.info = {"longName": "Invalid Stock Berhad"}

    with patch.dict(os.environ, {}, clear=True), patch("tools.settings.TAVILY_API_KEY", ""):
      result = YFinanceUtils.get_quarterly_reports("0000.KL")

      assert result["status"] == "FAILED"
      assert result["source"] == "none"
      assert "TAVILY_API_KEY is not set" in result["error"]


def test_quarterly_reports_double_checks_yfinance_income_statement():
  with patch("yfinance.Ticker") as mock_ticker:
    mock_instance = mock_ticker.return_value
    mock_instance.info = {"longName": "Example Berhad"}
    mock_instance.quarterly_financials = pd.DataFrame(
        {
            "2026-04-30": {
                "Total Revenue": 100_000_000,
                "Net Income": 5_000_000,
            }
        }
    )
    mock_instance.quarterly_income_stmt = pd.DataFrame(
        {
            "2026-04-30": {
                "Total Revenue": 110_000_000,
                "Net Income": 5_000_000,
            }
        }
    )

    with patch.dict(os.environ, {}, clear=True), patch("tools.settings.TAVILY_API_KEY", ""):
      result = YFinanceUtils.get_quarterly_reports("1234.KL")

    review = result["data_quality_review"]
    assert review["status"] == "MISMATCH"
    assert review["mismatches"][0]["field"] == "Total Revenue"
    assert review["tavily_evidence"]["status"] == "SKIPPED"


def test_quarterly_reports_flags_eps_sanity_warning_even_when_fields_match():
  with patch("yfinance.Ticker") as mock_ticker:
    mock_instance = mock_ticker.return_value
    mock_instance.info = {"longName": "Example Berhad"}
    quarter = {
        "Total Revenue": 100_000_000,
        "Net Income": 5_000_000,
        "Diluted Average Shares": 100_000_000,
        "Diluted EPS": 0.0,
    }
    mock_instance.quarterly_financials = pd.DataFrame({"2026-04-30": quarter})
    mock_instance.quarterly_income_stmt = pd.DataFrame({"2026-04-30": quarter})

    with patch.dict(os.environ, {}, clear=True), patch("tools.settings.TAVILY_API_KEY", ""):
      result = YFinanceUtils.get_quarterly_reports("1234.KL")

    review = result["data_quality_review"]
    assert review["status"] == "WARNING"
    assert "EPS is 0.0 while net income is positive" in review["warnings"][0]


def test_quarterly_reports_attaches_tavily_evidence_when_available():
  with patch("yfinance.Ticker") as mock_ticker, patch("tools.TavilyClient") as mock_tavily:
    mock_instance = mock_ticker.return_value
    mock_instance.info = {"longName": "Example Berhad"}
    quarter = {"Total Revenue": 100_000_000, "Net Income": 5_000_000}
    mock_instance.quarterly_financials = pd.DataFrame({"2026-04-30": quarter})
    mock_instance.quarterly_income_stmt = pd.DataFrame({"2026-04-30": quarter})
    mock_tavily.return_value.search.return_value = {
        "results": [
            {
                "title": "Example quarterly results",
                "url": "https://example.test/results",
                "content": "Revenue RM100m net profit RM5m EPS 0.05",
                "published_date": "2026-05-30",
            }
        ]
    }

    with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}, clear=True):
      result = YFinanceUtils.get_quarterly_reports("1234.KL")

    evidence = result["data_quality_review"]["tavily_evidence"]
    assert evidence["status"] == "SUCCESS"
    assert "quarterly financial results" in evidence["query"]
    assert evidence["results"][0]["url"] == "https://example.test/results"


def test_dcf_calculator_edge_cases():
  """Failure Case: Test DCF calculation with negative/zero PE Ratio using .invoke()."""
  # Invoke LangChain tool using .invoke() with a dictionary input
  result_zero_pe = calculate_dcf_val.invoke(
      {"current_price": 1.00, "pe_ratio": 0.0}
  )
  assert result_zero_pe["estimated_fair_value_myr"] > 0
  assert result_zero_pe["pe_ratio_substituted"] is True

  result_none_pe = calculate_dcf_val.invoke(
      {"current_price": 0.50, "pe_ratio": None}
  )
  assert result_none_pe["estimated_fair_value_myr"] > 0
  assert result_none_pe["pe_ratio_substituted"] is True
  assert result_none_pe["wacc"] == 0.08
  assert result_none_pe["terminal_growth_rate"] == 0.02
  assert len(result_none_pe["projected_eps"]) == 5
  assert len(result_none_pe["projected_fcff_per_share"]) == 5
