"""Display retrieved statements without asking a model to reconstruct values."""

from app.research_tools import current_evidence
from app.evidence import unwrap

STATEMENTS = (
    ("income_statement", "Income Statement"),
    ("balance_sheet", "Balance Sheet"),
    ("cash_flow", "Cash Flow"),
)


def financial_statements(messages):
    companies = {}
    for message in current_evidence(messages):
        if message.name != "market_data" or message.status == "error":
            continue
        try:
            payload, metadata = unwrap(message)
        except (ValueError, TypeError, KeyError):
            continue
        if (
            not isinstance(payload, dict)
            or payload.get("error")
            or not isinstance(payload.get("symbol"), str)
        ):
            continue
        if not any(
            isinstance(payload.get(key), dict) and payload[key] for key, _ in STATEMENTS
        ):
            continue
        companies[payload["symbol"]] = {
            **{key: payload.get(key, {}) for key, _ in STATEMENTS},
            "symbol": payload["symbol"],
            "currency": payload.get("currency"),
            "timestamp": payload.get("timestamp"),
            "source": "Yahoo Finance",
            "frequency": payload.get("frequency", "Annual"),
            "as_of": payload.get("as_of"),
        }
    return list(companies.values())
