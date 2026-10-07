"""Analyze explicitly dated, provider-reported statements without filling gaps."""
from datetime import date
from .data_processor import CompanyInfo, FinancialPeriod, DataProcessor, DataSource, ReportingStandard


def analyze_statements(payload, period_end=None):
    if not payload.get("ok"):
        raise ValueError(payload.get("error", "Statement retrieval failed"))
    selected = period_end or payload["latest_complete_period"]
    period = date.fromisoformat(selected)
    frequency = payload["frequency"]
    company = CompanyInfo(payload["symbol"], payload["company_name"], "", "", "",
                          ReportingStandard.UNKNOWN, period.strftime("%m-%d"), payload["reporting_currency"])
    processed = DataProcessor().process_data(
        payload, DataSource.API, company,
        FinancialPeriod(period, "annual" if frequency == "yearly" else "quarterly", period.year,
                        "FY" if frequency == "yearly" else "interim", audit_status="not_verified"),
    )
    return {"ok": True, "kind": "financial_analysis", "symbol": payload["symbol"],
            "company_name": payload["company_name"], "period_end": selected,
            "frequency": frequency, "currency": payload["reporting_currency"], "units": 1,
            "income_statement": processed.income_statement, "balance_sheet": processed.balance_sheet,
            "cash_flow": processed.cash_flow, "ratios": processed.ratios,
            "data_quality": processed.data_quality, "source_url": payload["source_url"],
            "retrieved_at_utc": payload["retrieved_at_utc"], "provider": payload["provider"],
            "audit_status": "not_verified", "cross_verified": False,
            "limitations": payload["limitations"] + ["Margins and liquidity ratios are calculated from this reporting period, not forecasts."]}