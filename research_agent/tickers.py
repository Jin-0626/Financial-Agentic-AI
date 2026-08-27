import yfinance as yf
import re
from research_agent.schemas import BursaTickerRequest

MAX_BURSA_NUMERIC_CODE_LENGTH = 4


def resolve_bursa_ticker(symbol_or_name: str) -> str:
    """Dynamically resolve a stock name, keyword, or code into a Bursa Malaysia (.KL) ticker symbol

    using yfinance's Search API.
    """
    clean = str(symbol_or_name).strip()
    if not clean:
        return ""

    clean_upper = clean.upper()

    # 1. Direct match if input already contains a 4-digit Bursa code (e.g. "3182" or "3182.KL")
    match = re.search(r"\b\d{4}\b", clean_upper)
    if match:
        return f"{match.group(0)}.KL"

    if clean_upper.endswith(".KL"):
        return clean_upper



    # 3. Dynamic lookup via yfinance Search API
    try:
        search_query = f"{clean} Bursa" if "bursa" not in clean.lower() else clean
        search_results = yf.Search(search_query, max_results=10).quotes

        for item in search_results:
            symbol = item.get("symbol", "")
            exchange = item.get("exchange", "") or item.get("exchDisp", "")

            # Match Bursa Malaysia quotes (.KL or KLS/KLSE exchange)
            if symbol.endswith(".KL") or exchange in {"KLS", "KLSE", "Kuala Lumpur"}:
                ticker = symbol if symbol.endswith(".KL") else f"{symbol}.KL"

          
                return ticker

    except Exception:
        pass

    # 4. Fallback string formatting if search returns no matches
    return f"{clean_upper}.KL"