from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest


ROOT_DIR = Path(__file__).parent.parent
APP_PATH = ROOT_DIR / "app.py"
pytestmark = pytest.mark.failure


def test_app_invalid_stock_code():
  """Failure Case: Run the Streamlit UI with a nonexistent stock code."""
  fake_info = {
      "symbol": "NONEXISTENT_STOCK_123.KL",
      "company_name": "Invalid Stock",
      "current_price": None,
      "pe_ratio": None,
      "dividend_yield": None,
      "sector": "N/A",
      "industry": "N/A",
  }
  with (
      patch("tools.YFinanceUtils.search_stock_by_name", return_value=[]),
      patch("tools.YFinanceUtils.get_stock_info", return_value=fake_info),
      patch("tools.YFinanceUtils.get_stock_history", return_value=pd.DataFrame()),
  ):
    at = AppTest.from_file(str(APP_PATH)).run()

    at.text_input[0].input("NONEXISTENT_STOCK_123").run()

    assert not at.exception
