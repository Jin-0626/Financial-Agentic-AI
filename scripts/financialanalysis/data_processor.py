"""Financial Statement Data Processor Module.

Standardizes, validates, and prepares multi-source financial statements.
"""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
import logging
import math
import re
from typing import Any, Dict, List, Optional
import pandas as pd


class ReportingStandard(Enum):
  UNKNOWN = "UNKNOWN"
  IFRS = "IFRS"
  US_GAAP = "US_GAAP"
  LOCAL_GAAP = "LOCAL_GAAP"


class DataSource(Enum):
  API = "api"
  CSV = "csv"
  EXCEL = "excel"
  JSON = "json"
  MANUAL = "manual"
  TERMINAL = "terminal"


@dataclass
class CompanyInfo:
  ticker: str
  name: str
  sector: str
  industry: str
  country: str
  reporting_standard: ReportingStandard
  fiscal_year_end: str
  presentation_currency: str
  functional_currency: Optional[str] = None
  exchange: Optional[str] = None
  market_cap: Optional[float] = None


@dataclass
class FinancialPeriod:
  period_end: date
  period_type: str  # 'annual', 'quarterly', 'interim'
  fiscal_year: int
  fiscal_period: str  # 'Q1', 'Q2', 'Q3', 'Q4', 'FY'
  reporting_date: Optional[date] = None
  audit_status: Optional[str] = None  # 'audited', 'reviewed', 'unaudited'


@dataclass
class FinancialStatements:
  company_info: CompanyInfo
  period_info: FinancialPeriod
  income_statement: Dict[str, float] = field(default_factory=dict)
  balance_sheet: Dict[str, float] = field(default_factory=dict)
  cash_flow: Dict[str, float] = field(default_factory=dict)
  equity_statement: Dict[str, float] = field(default_factory=dict)
  notes: Dict[str, Any] = field(default_factory=dict)
  ratios: Dict[str, float] = field(default_factory=dict)
  data_quality: Dict[str, Any] = field(default_factory=dict)


class DataProcessor:

  def __init__(self):
    self.logger = logging.getLogger(__name__)
    self._init_mappings()

  def _init_mappings(self):
    self.income_statement_mapping = {
        "revenue": [
            "revenue",
            "sales",
            "net_sales",
            "total_revenue",
            "net_revenue",
        ],
        "cost_of_sales": [
            "cost_of_sales",
            "cost_of_goods_sold",
            "cogs",
            "cost_of_revenue",
        ],
        "gross_profit": ["gross_profit", "gross_income"],
        "operating_expenses": [
            "operating_expenses",
            "total_operating_expenses",
            "opex",
        ],
        "selling_expenses": [
            "selling_expenses",
            "sales_expenses",
            "marketing_expenses",
        ],
        "administrative_expenses": [
            "administrative_expenses",
            "admin_expenses",
            "general_admin",
            "sga",
        ],
        "rd_expenses": ["research_development", "rd_expenses", "r_and_d"],
        "depreciation": [
            "depreciation",
            "depreciation_amortization",
            "da_expense",
        ],
        "operating_income": ["operating_income", "operating_profit"],
        "ebit": ["ebit"],
        "interest_expense": [
            "interest_expense",
            "interest_cost",
            "finance_costs",
        ],
        "interest_income": ["interest_income", "interest_revenue"],
        "other_income": ["other_income", "other_revenue", "non_operating_income"],
        "pretax_income": ["pretax_income", "ebt", "income_before_tax"],
        "tax_expense": ["tax_expense", "income_tax", "provision_for_taxes", "tax_provision"],
        "net_income": ["net_income", "net_profit", "profit_after_tax"],
        "net_income_attributable": ["net_income_common_stockholders", "net_income_attributable_to_owners"],
        "ebitda": ["ebitda"],
        "basic_eps": ["basic_eps", "earnings_per_share_basic", "eps"],
        "diluted_eps": ["diluted_eps", "earnings_per_share_diluted"],
        "shares_outstanding_basic": [
            "shares_outstanding_basic",
            "basic_shares",
            "basic_average_shares",
        ],
        "shares_outstanding_diluted": [
            "shares_outstanding_diluted",
            "diluted_shares",
            "diluted_average_shares",
        ],
    }

    self.balance_sheet_mapping = {
        "cash_only": ["cash"],
        "cash_equivalents": ["cash_equivalents"],
        "cash_and_cash_equivalents": ["cash_and_cash_equivalents", "cash_and_equivalents"],
        "short_term_investments": [
            "short_term_investments",
            "marketable_securities",
        ],
        "accounts_receivable": [
            "accounts_receivable",
            "receivables",
            "trade_receivables",
        ],
        "inventory": ["inventory", "inventories"],
        "current_assets": ["current_assets", "total_current_assets"],
        "ppe_net": ["ppe_net", "property_plant_equipment_net", "fixed_assets"],
        "intangible_assets": ["intangible_assets", "intangibles"],
        "goodwill": ["goodwill"],
        "total_assets": ["total_assets", "assets"],
        "total_payables": ["payables"],
        "accounts_payable": ["accounts_payable", "trade_payables"],
        "short_term_debt": [
            "short_term_debt",
            "current_debt",
            "notes_payable_current",
        ],
        "current_liabilities": [
            "current_liabilities",
            "total_current_liabilities",
        ],
        "long_term_debt": ["long_term_debt", "non_current_debt"],
        "total_liabilities": ["total_liabilities", "liabilities", "total_liabilities_net_minority_interest"],
        "common_stock": ["common_stock", "share_capital", "capital_stock"],
        "retained_earnings": ["retained_earnings"],
        "total_equity": ["total_equity", "total_equity_gross_minority_interest"],
        "parent_equity": ["shareholders_equity", "stockholders_equity"],
        "minority_interest": ["minority_interest"],
    }

    self.cash_flow_mapping = {
        "operating_cash_flow": [
            "operating_cash_flow",
            "cash_from_operations",
            "cfo",
        ],
        "capex": [
            "capital_expenditures",
            "capital_expenditure",
            "capex",
            "ppe_investments",
            "additions_to_property_plant_and_equipment",
        ],
        "investing_cash_flow": [
            "investing_cash_flow",
            "cash_from_investing",
            "cfi",
        ],
        "financing_cash_flow": [
            "financing_cash_flow",
            "cash_from_financing",
            "cff",
        ],
        "net_cash_change": ["net_cash_change", "net_change_cash", "changes_in_cash"],
        "cash_beginning": ["cash_beginning_period", "beginning_cash", "beginning_cash_position"],
        "cash_ending": ["cash_ending_period", "ending_cash", "end_cash_position"],
    }

  @staticmethod
  def _parse_numeric(val: Any) -> Optional[float]:
    """Missing/nonfinite values remain missing; accounting negatives are preserved."""
    if val is None or isinstance(val, (bool, list, dict, tuple)):
      return None
    try:
      if pd.isna(val):
        return None
    except (TypeError, ValueError):
      return None
    cleaned = str(val).strip()
    if cleaned.lower() in {"", "-", "â€“", "â€”", "n/a", "na", "null", "none", "nan"}:
      return None
    if cleaned.startswith("(") and cleaned.endswith(")"):
      cleaned = "-" + cleaned[1:-1]
    cleaned = re.sub(r"^(?:RM|MYR|USD|\$)\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"[,\s]", "", cleaned)
    try:
      number = float(cleaned)
      return number if math.isfinite(number) else None
    except (ValueError, TypeError):
      return None


  def _load_data(self, data, source_type) -> Dict[str, Any]:
    """Accept one explicitly selected period; never silently select the first row."""
    if source_type in (DataSource.CSV, DataSource.EXCEL):
      data = pd.read_csv(data) if source_type == DataSource.CSV else pd.read_excel(data)
    if source_type == DataSource.JSON and isinstance(data, str):
      import json
      with open(data, encoding="utf-8") as stream:
        data = json.load(stream)
    if isinstance(data, pd.DataFrame):
      if data.empty:
        raise ValueError("Provided data contains no rows")
      columns = [str(c).lower() for c in data.columns]
      if columns == ["metric", "value"] or columns == ["account", "value"]:
        if data.iloc[:, 0].duplicated().any():
          raise ValueError("Duplicate financial accounts")
        return dict(zip(data.iloc[:, 0], data.iloc[:, 1]))
      if len(data) != 1:
        raise ValueError("Select one reporting period before processing multiple rows")
      return data.to_dict("records")[0]
    if not isinstance(data, dict):
      raise ValueError("Financial data must be a dictionary or selected-period table")
    return data


  def _standardize_accounts(self, raw_data) -> Dict[str, Any]:
    normalize = lambda key: re.sub(r"[^a-z0-9]", "", str(key).lower())
    raw = {}
    for key, value in raw_data.items():
      normalized = normalize(key)
      if normalized in raw and raw[normalized] != value:
        raise ValueError(f"Conflicting duplicate account: {key}")
      raw[normalized] = value
    standardized = {}
    mappings = {**self.income_statement_mapping, **self.balance_sheet_mapping, **self.cash_flow_mapping}
    for target, variants in mappings.items():
      available = [self._parse_numeric(raw[normalize(v)]) for v in [target, *variants] if normalize(v) in raw]
      available = [v for v in available if v is not None]
      if available:
        if any(not math.isclose(available[0], v, rel_tol=1e-9, abs_tol=1e-9) for v in available[1:]):
          raise ValueError(f"Conflicting values for {target}")
        standardized[target] = available[0]
    return standardized


  def _extract_income_statement(
      self, data: Dict[str, Any]
  ) -> Dict[str, float]:
    is_data = {
        k: float(data[k])
        for k in self.income_statement_mapping
        if k in data and data[k] is not None
    }

    if (
        "gross_profit" not in is_data
        and "revenue" in is_data
        and "cost_of_sales" in is_data
    ):
      is_data["gross_profit"] = is_data["revenue"] - abs(is_data["cost_of_sales"])

    if (
        "operating_income" not in is_data
        and "gross_profit" in is_data
        and "operating_expenses" in is_data
    ):
      is_data["operating_income"] = (
          is_data["gross_profit"] - abs(is_data["operating_expenses"])
      )

    return is_data

  def _extract_balance_sheet(self, data: Dict[str, Any]) -> Dict[str, float]:
    return {
        k: float(data[k])
        for k in self.balance_sheet_mapping
        if k in data and data[k] is not None
    }

  def _extract_cash_flow(self, data: Dict[str, Any]) -> Dict[str, float]:
    return {
        k: float(data[k])
        for k in self.cash_flow_mapping
        if k in data and data[k] is not None
    }

  def _validate_financial_statements(
      self, statements: FinancialStatements
  ) -> None:
    errors: List[str] = []
    warnings: List[str] = []

    bs = statements.balance_sheet
    if "total_equity" not in bs and "parent_equity" in bs and "minority_interest" in bs:
      bs["total_equity"] = bs["parent_equity"] + bs["minority_interest"]
      statements.notes["derived_total_equity"] = "parent_equity + minority_interest"
    if all(k in bs for k in ("total_assets", "total_liabilities", "total_equity")):
      diff = abs(bs["total_assets"] - (bs["total_liabilities"] + bs["total_equity"]))
      if diff > 1.0:  # Allow 1.0 unit margin for rounding across filings
        errors.append(
            f"Balance sheet out of balance by {diff:.2f}: Assets ({bs['total_assets']}) "
            f"!= Liab ({bs['total_liabilities']}) + Equity ({bs['total_equity']})"
        )
    else:
      warnings.append(
          "Incomplete Balance Sheet: Verification of Assets = Liabilities +"
          " Equity skipped."
      )

    cf = statements.cash_flow
    if all(k in cf for k in ("net_cash_change", "cash_beginning", "cash_ending")):
      implied_change = cf["cash_ending"] - cf["cash_beginning"]
      if abs(cf["net_cash_change"] - implied_change) > 1.0:
        warnings.append(
            f"Cash flow reconciliation discrepancy: delta ({cf['net_cash_change']}) "
            f"!= ending - beginning ({implied_change})"
        )

    inc = statements.income_statement
    if all(
        k in inc
        for k in ("basic_eps", "net_income", "shares_outstanding_basic")
    ):
      if inc["shares_outstanding_basic"] > 0:
        implied_eps = inc.get("net_income_attributable", inc["net_income"]) / inc["shares_outstanding_basic"]
        if not math.isclose(inc["basic_eps"], implied_eps, rel_tol=0.01, abs_tol=0.00005):
          warnings.append(
              f"EPS discrepancy: Reported {inc['basic_eps']:.2f} vs Implied"
              f" {implied_eps:.2f}"
          )

    statements.data_quality["validation_errors"] = errors
    statements.data_quality["validation_warnings"] = warnings

    if errors:
      raise ValueError(f"Integrity check failed: {'; '.join(errors)}")

  def process_data(self, data, source_type, company_info, period_info, *,
                   monetary_unit_multiplier=1, shares_unit_multiplier=1) -> FinancialStatements:
    for multiplier in (monetary_unit_multiplier, shares_unit_multiplier):
      if isinstance(multiplier, bool) or not isinstance(multiplier, (int, float)) or not math.isfinite(multiplier) or multiplier <= 0:
        raise ValueError("Unit multipliers must be finite positive numbers")
    if period_info.period_end > date.today():
      raise ValueError("Reporting period is in the future")
    raw = self._load_data(data, source_type)
    provenance = {}
    if "income_statement" in raw and isinstance(raw["income_statement"], dict):
      if raw.get("symbol") != company_info.ticker or raw.get("reporting_currency") != company_info.presentation_currency:
        raise ValueError("Statement company identity or presentation currency mismatch")
      expected_frequency = "yearly" if period_info.period_type == "annual" else "quarterly"
      if raw.get("frequency") != expected_frequency or raw.get("units") != monetary_unit_multiplier:
        raise ValueError("Statement reporting frequency or monetary units mismatch")
      period = period_info.period_end.isoformat()
      if period not in raw.get("common_periods", []):
        raise ValueError("Requested reporting period is not complete across all statements")
      provenance = {k: raw.get(k) for k in ["source_url", "retrieved_at_utc", "provider", "reporting_currency", "audit_status"]}
      flattened = {}
      for section in ["income_statement", "balance_sheet", "cash_flow"]:
        for key, value in raw[section][period].items():
          if key in flattened and flattened[key] is not None and value is not None and flattened[key] != value:
            raise ValueError(f"Conflicting statement account across sections: {key}")
          if value is not None:
            flattened[key] = value
      raw = flattened
    std = self._standardize_accounts(raw)
    for key in std:
      if key in {"basic_eps", "diluted_eps"}:
        continue
      std[key] *= shares_unit_multiplier if key.startswith("shares_outstanding") else monetary_unit_multiplier
    statements = FinancialStatements(company_info=company_info, period_info=period_info)
    statements.income_statement = self._extract_income_statement(std)
    statements.balance_sheet = self._extract_balance_sheet(std)
    statements.cash_flow = self._extract_cash_flow(std)
    statements.notes = {"provenance": provenance, "monetary_unit_multiplier": monetary_unit_multiplier,
                        "shares_unit_multiplier": shares_unit_multiplier, "ratios_are_percent": True}
    self._validate_financial_statements(statements)
    inc, bs, cf = statements.income_statement, statements.balance_sheet, statements.cash_flow
    revenue = inc.get("revenue")
    if revenue is not None and revenue > 0:
      for field, name in [("gross_profit", "gross_margin_pct"), ("operating_income", "operating_margin_pct"),
                          ("net_income", "net_margin_pct"), ("ebitda", "ebitda_margin_pct")]:
        if field in inc:
          statements.ratios[name] = inc[field] / revenue * 100
    if "operating_cash_flow" in cf and "capex" in cf:
      cf["free_cash_flow"] = cf["operating_cash_flow"] - abs(cf["capex"])
    if bs.get("current_liabilities", 0) > 0 and "current_assets" in bs:
      statements.ratios["current_ratio"] = bs["current_assets"] / bs["current_liabilities"]
    return statements

