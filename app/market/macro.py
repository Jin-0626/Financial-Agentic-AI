import logging
import os
from app.market import common
from app.market import quotes
from app.market import transport

logger = logging.getLogger(__name__)

YIELDS = {
    "1M": "DGS1MO",
    "3M": "DGS3MO",
    "6M": "DGS6MO",
    "1Y": "DGS1",
    "2Y": "DGS2",
    "5Y": "DGS5",
    "10Y": "DGS10",
    "30Y": "DGS30",
}


def fred_series(series_id, vintage=None):
    key = os.getenv("FRED_API_KEY", "")
    if not key:
        raise ValueError("FRED_API_KEY is required for official release observations")
    return transport.fetch(
        "https://api.stlouisfed.org/fred/series/observations",
        {
            "series_id": series_id,
            "api_key": key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 24,
            **({"realtime_start": vintage, "realtime_end": vintage} if vintage else {}),
        },
    ).get("observations", [])


def macro():
    rows = []
    limitations = []
    for label, ticker in (
        ("VIX", "^VIX"),
        ("S&P 500", "^GSPC"),
        ("Bursa KLCI", "^KLSE"),
        ("Gold proxy", "GLD"),
        ("Oil proxy", "USO"),
    ):
        try:
            value = quotes.quote("yahoo:" + ticker)
            rows.append(
                {
                    "name": label,
                    "kind": "index" if ticker.startswith("^") else "ETF proxy",
                    **value["data"],
                    "source": value["source"],
                    "observed_at": value["observed_at"],
                }
            )
        except Exception:  # noqa: BLE001 -- isolate third-party provider failures; expose partial coverage
            limitations.append(label + " unavailable")
    for maturity, series_id in YIELDS.items():
        if not os.getenv("FRED_API_KEY"):
            break
        try:
            observation = next(
                (row for row in fred_series(series_id) if row.get("value") != "."), None
            )
            if observation:
                rows.append(
                    {
                        "name": "Treasury " + maturity,
                        "kind": "yield",
                        "price": observation["value"],
                        "unit": "percent",
                        "observed_at": observation["date"],
                        "source": "FRED / US Treasury",
                        "series_id": series_id,
                    }
                )
        except Exception:  # noqa: BLE001 -- isolate third-party provider failures; expose partial coverage
            limitations.append("Yield " + maturity + " unavailable")
    if not os.getenv("FRED_API_KEY"):
        limitations.append(
            "Treasury yield curve requires FRED_API_KEY; fiscal interest records are not yield quotes"
        )
    try:
        raw = transport.fetch(
            "https://api.bnm.gov.my/public/opr",
            headers={"Accept": "application/vnd.BNM.API.v1+json"},
        )
        values = raw.get("data", [])
        values = values if isinstance(values, list) else [values]
        values = sorted(
            (row for row in values if isinstance(row, dict)),
            key=lambda row: str(row.get("date") or ""),
        )
        for row in values[-1:]:
            rows.append(
                {
                    "name": "Malaysia OPR",
                    "kind": "policy rate",
                    "price": str(row.get("rate", row.get("opr")))
                    if row.get("rate", row.get("opr")) is not None
                    else None,
                    "unit": "percent",
                    "observed_at": row.get("date"),
                    "source": "Bank Negara Malaysia",
                }
            )
    except Exception:  # noqa: BLE001 -- isolate third-party provider failures; expose partial coverage
        limitations.append("BNM OPR unavailable")
    return common.result(
        rows,
        "Yahoo Finance / FRED / BNM",
        "Dated index snapshots, ETF proxies and official daily/policy observations",
        limitations=limitations,
    )
