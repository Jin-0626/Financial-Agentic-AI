"""Provider normalization for the native engine; no automatic agent cutover.

Missing data remains missing. Currency must be supplied from provider metadata,
not guessed from a ticker suffix. Adjusted closes retain provider precision.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd

ACCOUNTS = {
    "Total Revenue": "revenue",
    "Gross Profit": "gross_profit",
    "Operating Income": "operating_income",
    "Net Income": "net_income",
    "EBITDA": "ebitda",
    "Current Assets": "current_assets",
    "Current Liabilities": "current_liabilities",
    "Operating Cash Flow": "operating_cash_flow",
    "Capital Expenditure": "capital_expenditure",
}


def _base(org_id: str, ticker: str, currency: str, as_of: str, dataset: dict) -> dict:
    if (
        not org_id
        or len(org_id) > 128
        or not re.fullmatch(r"[A-Z0-9.^=_-]{1,32}", ticker)
    ):
        raise ValueError("Invalid snapshot identity")
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("Provider currency is required")
    return {
        "schema_version": 1,
        "org_id": org_id,
        "ticker": ticker,
        "currency": currency,
        "unit_multiplier": "1",
        "provider": "yahoo",
        "source_reference": f"https://finance.yahoo.com/quote/{ticker}",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of,
        "dataset": dataset,
    }


def prices_snapshot(
    frame: pd.DataFrame, *, org_id: str, ticker: str, currency: str
) -> dict:
    """Require a frame fetched with auto_adjust=True; reject duplicates and gaps."""
    if frame.empty or "Close" not in frame or len(frame) < 3 or len(frame) > 10001:
        raise ValueError("Insufficient adjusted prices")
    bars = []
    previous = None
    for index, row in frame.iterrows():
        day = pd.Timestamp(index).date().isoformat()
        close = float(row["Close"])
        if (
            (previous is not None and day <= previous)
            or not math.isfinite(close)
            or close <= 0
        ):
            raise ValueError("Invalid or unordered adjusted prices")
        raw_volume = row.get("Volume")
        volume = None if raw_volume is None or pd.isna(raw_volume) else int(raw_volume)
        if volume is not None and (volume < 0 or float(raw_volume) != volume):
            raise ValueError("Invalid volume")
        bars.append({"date": day, "adjusted_close": close, "volume": volume})
        previous = day
    return _base(
        org_id, ticker, currency, bars[-1]["date"], {"kind": "prices", "bars": bars}
    )


def statements_snapshot(
    frames: list[pd.DataFrame],
    *,
    org_id: str,
    ticker: str,
    currency: str,
    annual: bool = True,
) -> dict:
    """Join provider statements on exact period dates, without filling missing accounts."""
    periods: dict[str, dict[str, str]] = {}
    for frame in frames:
        if frame.columns.duplicated().any() or frame.index.duplicated().any():
            raise ValueError("Duplicate statement labels or dates")
        for column in frame.columns:
            day = pd.Timestamp(column).date().isoformat()
            accounts = periods.setdefault(day, {})
            for label, name in ACCOUNTS.items():
                if label not in frame.index:
                    continue
                raw = frame.at[label, column]
                if pd.isna(raw):
                    continue
                try:
                    amount = Decimal(str(raw))
                except InvalidOperation as exc:
                    raise ValueError("Invalid monetary value") from exc
                if not amount.is_finite():
                    raise ValueError("Nonfinite monetary value")
                value = format(amount, "f")
                if name in accounts and Decimal(accounts[name]) != amount:
                    raise ValueError("Conflicting accounts in reporting period")
                accounts[name] = value
    if not periods or len(periods) > 80:
        raise ValueError("Statements unavailable")
    rows = [
        {"period": day, "annual": annual, "accounts": accounts}
        for day, accounts in sorted(periods.items())
    ]
    return _base(
        org_id,
        ticker,
        currency,
        rows[-1]["period"],
        {"kind": "statements", "periods": rows},
    )


def fetch_yahoo_snapshot(
    kind: str, *, org_id: str, ticker: str, lookback_days: int = 252
) -> dict:
    """Administrative ingestion using the existing Yahoo provider dependency."""
    from app.providers import ticker_info, refresh_statement_cache

    if kind == "statements":
        refresh_statement_cache()
    provider, info = ticker_info(ticker)
    if kind == "prices":
        if not 2 <= lookback_days <= 10000:
            raise ValueError("Invalid lookback")
        # Calendar buffer preserves trading observations; never insert nontrading rows.
        from datetime import timedelta

        start = datetime.now(timezone.utc).date() - timedelta(
            days=lookback_days * 2 + 30
        )
        frame = provider.history(start=start.isoformat(), auto_adjust=True, timeout=15)
        return prices_snapshot(
            frame.tail(lookback_days + 1),
            org_id=org_id,
            ticker=ticker,
            currency=info.get("currency", ""),
        )
    if kind == "statements":
        return statements_snapshot(
            [provider.income_stmt, provider.balance_sheet, provider.cashflow],
            org_id=org_id,
            ticker=ticker,
            currency=info.get("financialCurrency", ""),
        )
    raise ValueError("Unsupported dataset kind")
