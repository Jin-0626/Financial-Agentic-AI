import yfinance as yf

from research_agent.schemas import BursaTickerRequest

MAX_BURSA_NUMERIC_CODE_LENGTH = 4


def resolve_bursa_ticker(symbol_or_name: str) -> str:
    """Resolve company names, numeric stock codes, or ticker strings to Yahoo .KL symbols."""
    clean_input = BursaTickerRequest(query=symbol_or_name).query.upper()
    if clean_input.endswith(".KL"):
        return clean_input
    if clean_input.isdigit() and len(clean_input) <= MAX_BURSA_NUMERIC_CODE_LENGTH:
        return f"{clean_input.zfill(4)}.KL"

    try:
        search_results = yf.Search(clean_input, max_results=8).quotes
        for quote in search_results:
            symbol = quote.get("symbol", "")
            exchange = quote.get("exchange", "") or quote.get("exchDisp", "")
            if symbol.endswith(".KL") or exchange in {"KLS", "KLSE", "Kuala Lumpur"}:
                return symbol if symbol.endswith(".KL") else f"{symbol}.KL"
    except Exception:  # noqa: BLE001 - yfinance search exceptions vary by backend/version.
        pass

    return f"{clean_input}.KL"
