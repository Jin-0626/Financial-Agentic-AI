import calendar
import re
import time
from datetime import date, datetime
from typing import Any, Dict, Iterable, Optional

from agent_library import (
    ANALYSIS_AGENT_PROMPT,
    BEAR_AGENT_PROMPT,
    BULL_AGENT_PROMPT,
    FEWSHOT_STYLE_GUIDE,
    JUDGE_AGENT_PROMPT,
    MODELING_AGENT_PROMPT,
    SYNTHESIS_AGENT_PROMPT,
)
from model import get_fast_llm, get_heavy_llm
from observability import (
    error_state,
    extract_token_usage,
    failed_dependency_state,
    response_text,
    summarize_telemetry,
    telemetry_span,
)
from schemas import (
    DebateBrief,
    DebateCaseResult,
    FailureState,
    FinancialMetricsResult,
    InvestmentVerdict,
    ModelingSummaryResult,
    ReplanDecision,
    ResearchPlan,
    WorkflowError,
)
from state import BursaAgentState
from tools import calculate_dcf_val, fetch_bursa_quarterly_reports, fetch_bursa_stock_data

fast_llm = get_fast_llm()
heavy_llm = get_heavy_llm()
LLM_MAX_RETRIES = 2
LLM_BACKOFF_SECONDS = 0.05
PROMPT_MAX_CHARS = 6000

FUNDAMENTAL_CONTEXT_KEYS = (
    "symbol",
    "company_name",
    "sector",
    "industry",
    "currency",
    "current_price",
    "pe_ratio",
    "forward_pe",
    "dividend_yield",
    "fifty_two_week_high",
    "fifty_two_week_low",
    "market_cap",
    "summary",
)

VALUATION_CONTEXT_KEYS = (
    "status",
    "estimated_fair_value_myr",
    "upside_downside_pct",
    "pe_ratio_used",
    "growth_rate",
    "discount_rate",
    "terminal_pe",
    "eps_input",
    "projected_eps",
    "projected_fcff_per_share",
    "pe_ratio_substituted",
    "pe_ratio_substitution_reason",
)


def _model_name(llm: Any) -> Optional[str]:
    return getattr(llm, "model", None) or getattr(llm, "model_name", None)


def _has_failed(state: BursaAgentState) -> bool:
    return state.get("workflow_status") == "FAILED" or bool(state.get("failed_stage"))


def _compact_text(value: Any, max_chars: int = 1200) -> str:
    text = value if isinstance(value, str) else str(value or "")
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _compress_prompt(prompt: str, max_chars: int = PROMPT_MAX_CHARS) -> tuple[str, Dict[str, Any]]:
    normalized = "\n".join(line.rstrip() for line in str(prompt).splitlines())
    original_chars = len(normalized)
    if original_chars <= max_chars:
        return normalized, {
            "compressed": False,
            "prompt_chars_original": original_chars,
            "prompt_chars_final": original_chars,
        }

    head = normalized[: int(max_chars * 0.65)].rstrip()
    tail = normalized[-int(max_chars * 0.25) :].lstrip()
    compressed = (
        f"{head}\n\n[CONTENT COMPRESSED: {original_chars - len(head) - len(tail)} chars omitted]\n\n{tail}"
    )
    return compressed, {
        "compressed": True,
        "prompt_chars_original": original_chars,
        "prompt_chars_final": len(compressed),
    }


def _pick_keys(data: Dict[str, Any], keys: tuple[str, ...]) -> Dict[str, Any]:
    return {key: data.get(key) for key in keys if data.get(key) not in (None, "", [], {})}


def _compact_quarterly_reports(quarterly: Dict[str, Any]) -> Dict[str, Any]:
    compact = _pick_keys(
        quarterly,
        ("source", "status", "fallback_used", "fallback_provider", "fallback_reason", "error", "data_quality_review"),
    )
    quarterly_financials = quarterly.get("quarterly_financials")
    if isinstance(quarterly_financials, dict) and quarterly_financials:
        compact["quarterly_summary"] = []
        aliases = {
            "revenue": ("Total Revenue", "Revenue"),
            "ebitda": ("EBITDA", "Normalized EBITDA"),
            "operating_income": ("Operating Income", "Operating Profit"),
            "net_income": ("Net Income", "Net Income Common Stockholders"),
            "eps": ("Basic EPS", "Diluted EPS"),
        }
        quarter_items = list(quarterly_financials.items())
        revenues = [
            _safe_float((rows or {}).get("Total Revenue") or (rows or {}).get("Revenue")) if isinstance(rows, dict) else None
            for _, rows in quarter_items
        ]
        for idx, (period, rows) in enumerate(quarter_items[:4]):
            rows = rows if isinstance(rows, dict) else {}
            item = {"period": _normalize_quarter_label(period)}
            for key, possible_names in aliases.items():
                value = next((rows.get(name) for name in possible_names if rows.get(name) is not None), None)
                if value is not None:
                    item[key] = _format_per_share(value) if key == "eps" else _format_financial_amount(value)
            revenue = revenues[idx] if idx < len(revenues) else None
            if revenue is not None:
                previous_revenue = revenues[idx + 1] if idx + 1 < len(revenues) else None
                if previous_revenue:
                    item["qoq_revenue_change_pct"] = _format_percent((revenue - previous_revenue) / previous_revenue)
                if idx + 4 < len(revenues) and revenues[idx + 4]:
                    yoy_base = revenues[idx + 4]
                    item["yoy_revenue_change_pct"] = _format_percent((revenue - yoy_base) / yoy_base)
                item["comparison_note"] = "QoQ compares adjacent quarters; YoY requires the same quarter one year earlier."
            compact["quarterly_summary"].append(item)
    reports = quarterly.get("extracted_reports") or []
    if reports:
        compact["extracted_reports"] = [
            {
                "title": _compact_text(report.get("title"), 160),
                "content": _compact_text(report.get("content"), 250),
                "url": report.get("url"),
            }
            for report in reports[:2]
        ]
    return compact


def _compact_raw_data(raw: Any) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    fundamentals = raw.get("fundamentals", {}) if isinstance(raw.get("fundamentals", {}), dict) else {}
    quarterly = raw.get("quarterly_reports", {}) if isinstance(raw.get("quarterly_reports", {}), dict) else {}
    compact_fundamentals = _pick_keys(fundamentals, FUNDAMENTAL_CONTEXT_KEYS)
    if "summary" in compact_fundamentals:
        compact_fundamentals["summary"] = _compact_text(compact_fundamentals["summary"], 300)
    return {
        "fundamentals": compact_fundamentals,
        "quarterly_reports": _compact_quarterly_reports(quarterly),
    }


def _compact_valuation_model(valuation_model: Any) -> Dict[str, Any]:
    valuation_model = valuation_model if isinstance(valuation_model, dict) else {}
    compact = _pick_keys(valuation_model, VALUATION_CONTEXT_KEYS)
    if valuation_model.get("summary_notes"):
        compact["summary_notes"] = _compact_text(valuation_model["summary_notes"], 400)
    return compact


def _compact_financial_metrics(financial_metrics: Any) -> Dict[str, Any]:
    financial_metrics = financial_metrics if isinstance(financial_metrics, dict) else {}
    return {
        key: _compact_text(value, 400) if key == "analysis_notes" else value
        for key, value in financial_metrics.items()
        if value not in (None, "", [], {})
    }


def _compact_research_plan(research_plan: Any) -> Dict[str, Any]:
    research_plan = research_plan if isinstance(research_plan, dict) else {}
    return {
        "objective": _compact_text(research_plan.get("research_objective"), 220),
        "data_quality": research_plan.get("data_quality"),
        "valuation_method": research_plan.get("valuation_method"),
        "data_gaps": research_plan.get("data_gaps", [])[:3],
        "risks_to_test": research_plan.get("risks_to_test", [])[:3],
        "replanning_events": research_plan.get("replanning_events", [])[:3],
        "recovery_actions": research_plan.get("recovery_actions", [])[:3],
    }


def _format_metric(value: Any, suffix: str = "") -> str:
    return "unavailable" if value is None else f"{value}{suffix}"


def _deterministic_analysis(raw: Dict[str, Any]) -> FinancialMetricsResult:
    fundamentals = raw.get("fundamentals", {}) if isinstance(raw, dict) else {}
    quarterly = raw.get("quarterly_reports", {}) if isinstance(raw, dict) else {}
    pe = fundamentals.get("pe_ratio")
    div_yield = fundamentals.get("dividend_yield")
    price = fundamentals.get("current_price")
    sector = fundamentals.get("sector", "N/A")
    source = quarterly.get("source", "none")
    quarterly_status = quarterly.get("status", "unknown")
    notes = (
        f"Price MYR {_format_metric(price)}; P/E {_format_metric(pe)}; "
        f"dividend yield {_format_metric(div_yield, '%')}; sector {sector}. "
        f"Quarterly data source {source} with status {quarterly_status}."
    )
    return FinancialMetricsResult(
        analysis_notes=notes,
        pe_ratio=pe,
        div_yield=div_yield,
    )


def _build_research_plan(state: BursaAgentState) -> ResearchPlan:
    raw = state.get("raw_data", {})
    fundamentals = raw.get("fundamentals", {}) if isinstance(raw, dict) else {}
    quarterly = raw.get("quarterly_reports", {}) if isinstance(raw, dict) else {}
    ticker = state.get("ticker", fundamentals.get("symbol", "N/A"))
    company_name = state.get("company_name") or fundamentals.get("company_name") or ticker

    data_gaps = []
    recovery_actions = []
    if not fundamentals and not quarterly:
        data_gaps.append("Market and quarterly data pending; data_agent has not run yet.")
    elif fundamentals.get("current_price") is None:
        data_gaps.append("Current market price unavailable.")
    if fundamentals and fundamentals.get("pe_ratio") is None:
        data_gaps.append("Trailing P/E unavailable; valuation may require a proxy.")
    if quarterly and not quarterly.get("quarterly_financials"):
        data_gaps.append("Structured quarterly financial table unavailable.")
    if quarterly.get("fallback_used"):
        data_gaps.append(
            f"Quarterly data used fallback provider: {quarterly.get('fallback_provider', 'unknown')}."
        )
    data_review = quarterly.get("data_quality_review") if isinstance(quarterly, dict) else None
    review_status = data_review.get("status") if isinstance(data_review, dict) else None
    tavily_evidence = data_review.get("tavily_evidence") if isinstance(data_review, dict) else None
    tavily_status = tavily_evidence.get("status") if isinstance(tavily_evidence, dict) else None
    if review_status == "MISMATCH":
        mismatch_count = len(data_review.get("mismatches") or [])
        data_gaps.append(f"Quarterly financial double-check found {mismatch_count} field mismatch(es).")
        recovery_actions.append("Treat yfinance quarterly figures as provisional and verify against Bursa filings.")
    elif review_status == "WARNING":
        warning_count = len(data_review.get("warnings") or [])
        data_gaps.append(f"Quarterly financial double-check produced {warning_count} warning(s).")
        recovery_actions.append("Review EPS/share-count sanity warnings before relying on EPS-driven conclusions.")
    elif review_status == "UNCHECKED":
        data_gaps.append("Quarterly financials could not be double-checked against an alternate yfinance statement.")
        recovery_actions.append("Use quarterly figures with caution and verify against Bursa filings before relying on the forecast.")
    if tavily_status in {"FAILED", "EMPTY", "SKIPPED"}:
        data_gaps.append(f"Tavily external quarterly evidence search status: {tavily_status}.")
        recovery_actions.append("Do not treat yfinance-only figures as filing-verified when Tavily evidence is unavailable.")

    if not fundamentals and not quarterly:
        data_quality = "partial"
    elif review_status == "MISMATCH":
        data_quality = "degraded"
    elif review_status == "WARNING":
        data_quality = "partial"
    elif quarterly.get("fallback_used") or quarterly.get("source") == "none":
        data_quality = "degraded"
    elif data_gaps:
        data_quality = "partial"
    else:
        data_quality = "complete"

    risks = [
        "Validate whether DCF-implied fair value is supported by fundamentals.",
        "Test dividend support and valuation sensitivity against available P/E data.",
        "Compare bull and bear interpretations before issuing a final rating.",
    ]
    if data_gaps:
        risks.append("Flag data limitations in the final report.")

    return ResearchPlan(
        ticker=ticker,
        company_name=company_name,
        research_objective=(
            f"Produce a Bursa Malaysia company update for {company_name} with valuation, "
            "financial quality checks, adversarial debate, and a final investment call."
        ),
        required_agents=[
            "data_agent",
            "analysis_agent",
            "modeling_agent",
            "synthesis_agent",
            "bull_agent",
            "bear_agent",
            "debate_agent",
            "judge_agent",
            "replanner_agent",
            "report_agent",
        ],
        data_quality=data_quality,
        valuation_method="dcf_pe_proxy",
        debate_required=True,
        risks_to_test=risks,
        data_gaps=data_gaps,
        recovery_actions=recovery_actions,
    )


def _safe_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _format_price(value: Any, decimals: int = 3) -> str:
    number = _safe_float(value)
    return f"{number:.{decimals}f}" if number is not None else "N/A"


def _format_number(value: Any, decimals: int = 2) -> str:
    number = _safe_float(value)
    return f"{number:.{decimals}f}" if number is not None else "N/A"


def _format_percent(value: Any, decimals: int = 1) -> str:
    number = _safe_float(value)
    if number is None:
        return "N/A"
    if 0 < abs(number) < 1:
        number *= 100
    return f"{number:.{decimals}f}%"


def _format_market_cap(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "N/A"
    if abs(number) >= 1_000_000_000:
        return f"RM {number / 1_000_000_000:.2f}bn"
    if abs(number) >= 1_000_000:
        return f"RM {number / 1_000_000:.1f}m"
    return f"RM {number:,.0f}"


def _format_financial_amount(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "N/A"
    if abs(number) >= 1_000:
        millions = number / 1_000_000
        return f"{millions:.2f}" if abs(millions) < 1 else f"{millions:.1f}"
    return f"{number:.1f}"


def _format_per_share(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "N/A"
    return f"{number:.4f}"


def _normalize_report_text(value: Any) -> str:
    text = str(value or "")
    replacements = {
        "â€‘": "-",
        "â€“": "-",
        "â€”": "-",
        "â€™": "'",
        "â€˜": "'",
        "â€œ": '"',
        "â€�": '"',
        "â‰ˆ": "approx.",
        "â‰¥": ">=",
        "â‰¤": "<=",
        "â†’": "->",
        "â€¯": " ",
        "Ã—": "x",
        "Ã¨": "e",
        "Ã©": "e",
        "Ã§": "c",
        "Â": "",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u202f": " ",
        "\xa0": " ",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text


def _clean_report_section(value: Any, fallback: str, min_chars: int = 80) -> str:
    text = _normalize_report_text(value)
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text or text == "N/A" or len(text) < min_chars:
        return fallback
    if "â€" in text or "Ã" in text:
        return fallback
    if text[-1] not in ".!?":
        last_stop = max(text.rfind("."), text.rfind("!"), text.rfind("?"))
        if last_stop >= min_chars:
            text = text[: last_stop + 1]
        else:
            text = f"{text.rstrip()}."
    return text


def _deterministic_report_thesis(
    company_name: str,
    recommendation: str,
    price: Any,
    report_target: Any,
    pe: Any,
    div: Any,
    sector: str,
) -> str:
    return (
        f"{company_name} is rated {recommendation or 'N/A'} with a committee target price of "
        f"RM {_format_price(report_target, 2)} versus the current price of RM {_format_price(price)}. "
        f"The investment case is anchored by a P/E of {_format_number(pe)}x, dividend yield of "
        f"{_format_percent(div)}, and exposure to {sector}. The call remains evidence-weighted: "
        "valuation upside must be balanced against quarterly earnings quality and dividend sustainability."
    )


def _deterministic_bull_summary(company_name: str, report_target: Any, price: Any, div: Any) -> str:
    return (
        f"{company_name}'s bull case rests on valuation upside toward RM {_format_price(report_target, 2)}, "
        f"from a current price of RM {_format_price(price)}, plus dividend support of {_format_percent(div)}. "
        "Confirmation would require stable quarterly earnings, resilient margins, and evidence that cash flow "
        "can sustain dividends while funding growth."
    )


def _deterministic_bear_summary(company_name: str, low: Any) -> str:
    return (
        f"{company_name}'s bear case is that the valuation gap could be overstated if recent profit or EBITDA "
        "momentum weakens. A dividend cut, margin pressure, or lower earnings visibility could pull the share "
        f"price back toward the 52-week low of RM {_format_price(low)}."
    )


def _extract_verdict_line(raw_judgement: str, label: str) -> str:
    normalized_label = _normalize_report_text(label).lower().replace(" ", "").replace("-", "")
    for line in _normalize_report_text(raw_judgement).splitlines():
        clean = line.strip()
        if ":" not in clean:
            continue
        key, value = clean.split(":", 1)
        normalized_key = key.lower().replace(" ", "").replace("-", "")
        if normalized_key == normalized_label:
            return value.strip()
    return "N/A"


def _extract_verdict_price(raw_judgement: str, label: str) -> Optional[float]:
    value = _extract_verdict_line(raw_judgement, label)
    match = re.search(r"(\d+(?:\.\d+)?)", value.replace(",", ""))
    return float(match.group(1)) if match else None


def _derive_stop_loss(recommendation: str, entry_price: Any, low_52w: Any = None) -> Optional[float]:
    entry = _safe_float(entry_price)
    if entry is None:
        return None
    rating = str(recommendation or "").upper()
    low = _safe_float(low_52w)
    if rating == "BUY":
        return round(max(entry * 0.9, low or 0), 3)
    if rating == "SELL":
        return round(entry * 1.1, 3)
    if low is not None and low < entry:
        return round(max(low, entry * 0.94), 3)
    return round(entry * 0.94, 3)


def _normalize_quarter_label(label: Any) -> str:
    text = str(label)
    return text.split(" ")[0] if " " in text else text[:10]


def _quarterly_table_markdown(quarterly_financials: Any) -> str:
    if not isinstance(quarterly_financials, dict) or not quarterly_financials:
        return "Quarterly financial table unavailable from the current data provider."

    columns = list(quarterly_financials.keys())[:4]
    row_aliases: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("Revenue", ("Total Revenue", "Revenue")),
        ("EBITDA", ("EBITDA",)),
        ("Operating profit", ("Operating Income", "Operating Profit")),
        ("PBT", ("Pretax Income", "Income Before Tax")),
        ("Net income", ("Net Income", "Net Income Common Stockholders")),
    )

    lines = [
        "All items in RM m unless otherwise stated.",
        "",
        "| Item | " + " | ".join(_normalize_quarter_label(col) for col in columns) + " |",
        "| --- | " + " | ".join("---" for _ in columns) + " |",
    ]
    for label, aliases in row_aliases:
        values = []
        for col in columns:
            period = quarterly_financials.get(col, {})
            if not isinstance(period, dict):
                values.append("N/A")
                continue
            value = next((period.get(alias) for alias in aliases if alias in period), None)
            values.append(_format_financial_amount(value))
        lines.append(f"| {label} | " + " | ".join(values) + " |")

    return "\n".join(lines)


def _extract_quarterly_actual_rows(quarterly_financials: Any) -> list[dict[str, Any]]:
    if not isinstance(quarterly_financials, dict):
        return []

    rows = []
    for period, data in list(quarterly_financials.items())[:4]:
        if not isinstance(data, dict):
            continue
        rows.append(
            {
                "period": _normalize_quarter_label(period),
                "revenue": _safe_float(data.get("Total Revenue") or data.get("Revenue")),
                "ebitda": _safe_float(data.get("EBITDA") or data.get("Normalized EBITDA")),
                "operating_profit": _safe_float(data.get("Operating Income") or data.get("Operating Profit")),
                "pretax_profit": _safe_float(data.get("Pretax Income") or data.get("Income Before Tax")),
                "net_income": _safe_float(data.get("Net Income") or data.get("Net Income Common Stockholders")),
            }
        )
    return rows


def _average_margin(rows: list[dict[str, Any]], value_key: str) -> Optional[float]:
    margins = []
    for row in rows:
        revenue = row.get("revenue")
        value = row.get(value_key)
        if revenue and value is not None:
            margins.append(value / revenue)
    return sum(margins) / len(margins) if margins else None


def _derive_quarterly_growth(rows: list[dict[str, Any]]) -> float:
    revenues = [row["revenue"] for row in rows if row.get("revenue")]
    if len(revenues) < 2:
        return 0.0

    changes = []
    for newer, older in zip(revenues, revenues[1:]):
        if older:
            changes.append((newer / older) - 1)
    if not changes:
        return 0.0

    average_change = sum(changes) / len(changes)
    return max(min(average_change, 0.15), -0.15)


def _next_quarter_labels(latest_period: str, count: int = 4) -> list[str]:
    try:
        latest = datetime.fromisoformat(str(latest_period)[:10])
    except ValueError:
        return [f"Forecast Q{i}" for i in range(1, count + 1)]

    labels = []
    year = latest.year
    month = latest.month
    day = latest.day
    latest_is_month_end = latest.day == calendar.monthrange(latest.year, latest.month)[1]
    for _ in range(count):
        month += 3
        while month > 12:
            month -= 12
            year += 1
        forecast_day = calendar.monthrange(year, month)[1] if latest_is_month_end else min(day, 28)
        labels.append(date(year, month, forecast_day).isoformat())
    return labels


def _financial_forecast_markdown(quarterly_financials: Any) -> str:
    rows = _extract_quarterly_actual_rows(quarterly_financials)
    if not rows:
        return "Forward forecast unavailable because structured quarterly actuals are missing."

    latest_revenue = rows[0].get("revenue")
    if not latest_revenue:
        return "Forward forecast unavailable because latest quarterly revenue is missing."

    growth = _derive_quarterly_growth(rows)
    ebitda_margin = _average_margin(rows, "ebitda")
    operating_margin = _average_margin(rows, "operating_profit")
    net_margin = _average_margin(rows, "net_income")
    labels = _next_quarter_labels(rows[0]["period"])

    forecasts = []
    revenue = latest_revenue
    for label in labels:
        revenue *= 1 + growth
        forecasts.append(
            {
                "period": label,
                "revenue": revenue,
                "ebitda": revenue * ebitda_margin if ebitda_margin is not None else None,
                "operating_profit": revenue * operating_margin if operating_margin is not None else None,
                "net_income": revenue * net_margin if net_margin is not None else None,
            }
        )

    lines = [
        "Forecast is model-derived from recent quarterly actuals, not provider-reported guidance. All items in RM m unless otherwise stated.",
        "",
        "| Item | " + " | ".join(row["period"] for row in forecasts) + " |",
        "| --- | " + " | ".join("---" for _ in forecasts) + " |",
        "| Revenue | " + " | ".join(_format_financial_amount(row["revenue"]) for row in forecasts) + " |",
        "| EBITDA | " + " | ".join(_format_financial_amount(row["ebitda"]) for row in forecasts) + " |",
        "| Operating profit | " + " | ".join(_format_financial_amount(row["operating_profit"]) for row in forecasts) + " |",
        "| Net income | " + " | ".join(_format_financial_amount(row["net_income"]) for row in forecasts) + " |",
        "",
        f"Assumption: quarter-on-quarter revenue growth is capped at {_format_percent(growth)} based on the recent actual trend; margins use the average of available actual quarters.",
    ]
    return "\n".join(lines)


def _join_available(items: Iterable[str]) -> str:
    clean = [item for item in items if item and item != "N/A"]
    return " | ".join(clean) if clean else "N/A"


def _deterministic_debate_case(
    state: BursaAgentState,
    *,
    stance: str,
    fallback_reason: str,
) -> DebateCaseResult:
    raw = state.get("raw_data", {})
    fundamentals = raw.get("fundamentals", {}) if isinstance(raw, dict) else {}
    valuation = state.get("valuation_model", {}) if isinstance(state.get("valuation_model", {}), dict) else {}
    company_name = state.get("company_name") or fundamentals.get("company_name") or state.get("ticker", "the company")
    price = fundamentals.get("current_price")
    target = valuation.get("estimated_fair_value_myr")
    pe = fundamentals.get("pe_ratio")
    div = fundamentals.get("dividend_yield")
    sector = fundamentals.get("sector", "N/A")
    upside = valuation.get("upside_downside_pct")
    if upside is None:
        price_number = _safe_float(price)
        target_number = _safe_float(target)
        if price_number and target_number:
            upside = ((target_number - price_number) / price_number) * 100

    if stance == "bull":
        upside_number = _safe_float(upside)
        if upside_number is not None and upside_number < 0:
            content = (
                f"- Bull thesis: {company_name}'s constructive case depends on fundamentals improving enough "
                f"to close the current valuation deficit versus DCF fair value of RM {_format_price(target, 2)}.\n"
                f"- Evidence: The company still offers sector exposure to {sector}, P/E {_format_number(pe)}x, "
                f"and dividend yield {_format_percent(div)}, which may support income-oriented holders.\n"
                "- Upside catalyst: A material earnings upgrade, margin recovery, stronger dividends, or new project wins "
                "could justify a higher fair value than the current DCF proxy.\n"
                "- What would confirm it: Quarterly EBITDA/net income growth and cash-flow coverage strong enough to "
                "move fair value above market price."
            )
        else:
            content = (
                f"- Bull thesis: {company_name} may be attractive if the DCF fair value of "
                f"RM {_format_price(target, 2)} is credible versus the current price of RM {_format_price(price)}.\n"
                f"- Evidence: Available data shows P/E {_format_number(pe)}x, dividend yield {_format_percent(div)}, "
                f"and sector exposure to {sector}.\n"
                f"- Upside catalyst: Re-rating toward fair value would imply {_format_percent(upside)} capital movement before dividends.\n"
                "- What would confirm it: Cleaner quarterly revenue/profit delivery and sustained dividend support."
            )
    else:
        content = (
            f"- Bear thesis: {company_name}'s investment case is vulnerable if the DCF proxy overstates fair value "
            "or quarterly fundamentals do not support the valuation.\n"
            f"- Evidence: The model depends on P/E {_format_number(pe)}x and available structured data; "
            f"data quality is {state.get('research_plan', {}).get('data_quality', 'unknown')}.\n"
            "- Downside risk: Weak earnings momentum, lower dividend support, or valuation multiple compression could pressure returns.\n"
            "- What would invalidate it: Consistent quarterly growth and a defensible fair-value gap versus market price."
        )

    return DebateCaseResult(
        stance=stance,  # type: ignore[arg-type]
        content=content,
        source="deterministic_fallback",
        fallback_reason=fallback_reason,
    )


def _deterministic_modeling_summary(
    *,
    ticker: str,
    current_price: Any,
    pe_ratio: Any,
    dcf: Dict[str, Any],
    fallback_reason: str,
) -> ModelingSummaryResult:
    fair_value = dcf.get("estimated_fair_value_myr")
    upside = dcf.get("upside_downside_pct")
    pe_used = dcf.get("pe_ratio_used")
    substitution = "yes" if dcf.get("pe_ratio_substituted") else "no"
    reason = dcf.get("pe_ratio_substitution_reason") or "not applicable"
    content = (
        f"- Fair value read-through: {ticker} DCF proxy indicates fair value of "
        f"MYR {_format_price(fair_value, 2)} versus current price MYR {_format_price(current_price)}.\n"
        f"- Upside/downside: Implied capital movement is {_format_percent(upside)} before dividends.\n"
        f"- Key assumption risk: P/E used is {_format_number(pe_used)}x; growth {_format_percent(dcf.get('growth_rate'))}; "
        f"WACC {_format_percent(dcf.get('wacc'))}; terminal P/E {_format_number(dcf.get('terminal_pe'))}x; "
        f"P/E substituted: {substitution} ({reason}).\n"
        f"- Modeling caveat: LLM modeling commentary was unavailable, so this deterministic summary uses the DCF tool output only."
    )
    return ModelingSummaryResult(
        content=content,
        source="deterministic_fallback",
        fallback_reason=fallback_reason,
    )


def _deterministic_synthesis_summary(state: BursaAgentState, fallback_reason: str) -> ModelingSummaryResult:
    raw = state.get("raw_data", {})
    fundamentals = raw.get("fundamentals", {}) if isinstance(raw, dict) else {}
    quarterly = raw.get("quarterly_reports", {}) if isinstance(raw, dict) else {}
    valuation = state.get("valuation_model", {}) if isinstance(state.get("valuation_model", {}), dict) else {}
    company = state.get("company_name") or fundamentals.get("company_name") or state.get("ticker", "the company")
    summary = _compact_quarterly_reports(quarterly).get("quarterly_summary") or []
    latest = summary[0] if summary else {}
    qoq = latest.get("qoq_revenue_change_pct", "N/A")
    yoy = latest.get("yoy_revenue_change_pct", "N/A")
    eps = latest.get("eps", "N/A")
    content = (
        f"1. Thesis: {company} trades against a DCF fair value reference of RM "
        f"{_format_price(valuation.get('estimated_fair_value_myr'), 2)} versus current price RM "
        f"{_format_price(fundamentals.get('current_price'))}.\n"
        f"2. Key evidence: latest revenue RM {latest.get('revenue', 'N/A')}m, EPS {eps}; "
        f"QoQ revenue change {qoq}; YoY revenue change {yoy}. QoQ uses adjacent quarters and YoY uses the same quarter one year earlier.\n"
        f"3. Valuation view: implied upside/downside is {_format_percent(valuation.get('upside_downside_pct'))}; "
        f"DCF assumptions include WACC {_format_percent(valuation.get('wacc'))}, growth "
        f"{_format_percent(valuation.get('growth_rate'))}, and terminal P/E {_format_number(valuation.get('terminal_pe'))}x.\n"
        "4. Main uncertainty: verify external filings and treat the deterministic synthesis as a fallback because LLM synthesis was unavailable."
    )
    return ModelingSummaryResult(
        content=content,
        source="deterministic_fallback",
        fallback_reason=fallback_reason,
    )


def _build_debate_brief(state: BursaAgentState) -> DebateBrief:
    bull = _compact_text(state.get("bull_case", ""), 700)
    bear = _compact_text(state.get("bear_case", ""), 700)
    valuation = state.get("valuation_model", {}) if isinstance(state.get("valuation_model", {}), dict) else {}
    raw = state.get("raw_data", {})
    fundamentals = raw.get("fundamentals", {}) if isinstance(raw, dict) else {}
    fair_value = valuation.get("estimated_fair_value_myr")
    current_price = fundamentals.get("current_price")
    div = fundamentals.get("dividend_yield")

    contested_points = [
        "Whether the DCF fair value is supported by observed fundamentals.",
        "Whether dividend yield offsets valuation and earnings-quality risk.",
        "Whether recent quarterly data confirms or weakens the baseline thesis.",
    ]
    decision_questions = [
        f"Should target price anchor to fair value RM {_format_price(fair_value, 2)} or be discounted for evidence quality?",
        f"Is current price RM {_format_price(current_price)} attractive after considering dividend yield {_format_percent(div)}?",
        "Does the bear case identify enough downside risk to override the bull case?",
    ]
    return DebateBrief(
        bull_summary=bull,
        bear_summary=bear,
        contested_points=contested_points,
        decision_questions=decision_questions,
    )


def _fallback_metric(node: str, exc: Exception) -> Dict[str, Any]:
    with telemetry_span(node, model=_model_name(heavy_llm), llm_calls=1) as telemetry:
        telemetry.status = "fallback"
        telemetry.error_type = type(exc).__name__
        telemetry.fallback_used = True
        telemetry.fallback_provider = "deterministic_debate_case"
        telemetry.fallback_reason = str(exc) or type(exc).__name__
        return telemetry.as_dict()


def _mark_metric_fallback(metric: Dict[str, Any], *, provider: str, reason: str, error_type: str) -> Dict[str, Any]:
    metric["status"] = "fallback"
    metric["fallback_used"] = True
    metric["fallback_provider"] = provider
    metric["fallback_reason"] = reason
    metric["error_type"] = error_type
    return metric


def _build_replan_decision(state: BursaAgentState) -> ReplanDecision:
    metrics = list(state.get("node_metrics", []))
    fallback_nodes = [
        metric.get("node")
        for metric in metrics
        if metric.get("fallback_used") or metric.get("status") == "fallback"
    ]
    failed_nodes = [
        metric.get("node")
        for metric in metrics
        if metric.get("status") == "failed"
    ]
    fallback_nodes = [node for node in fallback_nodes if node]
    failed_nodes = [node for node in failed_nodes if node]

    recovery_actions = []
    replanning_events = []
    if fallback_nodes:
        joined = ", ".join(sorted(set(fallback_nodes)))
        replanning_events.append(f"Detected degraded node output from {joined}.")
        recovery_actions.append(
            "Continue with deterministic fallback outputs and require judge_agent to weigh them conservatively."
        )
    if failed_nodes:
        joined = ", ".join(sorted(set(failed_nodes)))
        replanning_events.append(f"Detected failed node telemetry from {joined}.")
        recovery_actions.append("Preserve upstream evidence and route final report to failure-safe disclosure if needed.")

    has_required_debate = bool(state.get("bull_case")) and bool(state.get("bear_case"))
    if not has_required_debate:
        replanning_events.append("Debate inputs incomplete before judge_agent.")
        recovery_actions.append("Block judge_agent until both bull_case and bear_case are available.")

    refreshed_plan = _build_research_plan(state)
    existing_quality = refreshed_plan.data_quality
    if failed_nodes or not has_required_debate:
        data_quality = "degraded"
    elif fallback_nodes and existing_quality == "complete":
        data_quality = "partial"
    else:
        data_quality = existing_quality if existing_quality in {"complete", "partial", "degraded"} else "partial"

    return ReplanDecision(
        triggered=bool(replanning_events),
        data_quality=data_quality,  # type: ignore[arg-type]
        recovery_actions=recovery_actions,
        replanning_events=replanning_events,
        judge_can_continue=has_required_debate and not failed_nodes,
    )


def _invoke_llm(
    node_name: str,
    llm: Any,
    prompt: str,
    *,
    downgrade_llm: Any = None,
    max_retries: int = LLM_MAX_RETRIES,
):
    compressed_prompt, compression_meta = _compress_prompt(prompt)
    active_llm = llm
    failures: list[Exception] = []
    attempt = 0
    downgrade_used = False

    with telemetry_span(
        node_name,
        model=_model_name(llm),
        llm_calls=0,
        metadata={"intercepted": True, **compression_meta},
    ) as telemetry:
        while True:
            telemetry.llm_calls += 1
            try:
                response = active_llm.invoke(compressed_prompt)
                content = response_text(response).strip()
                if not content:
                    raise ValueError(f"{node_name} returned an empty LLM response.")
                metric = telemetry.as_dict()
                metric.update(extract_token_usage(response))
                return content, metric
            except Exception as exc:
                failures.append(exc)
                telemetry.retry_count = len(failures)
                telemetry.error_type = type(exc).__name__
                if attempt < max_retries:
                    time.sleep(LLM_BACKOFF_SECONDS * (2**attempt))
                    attempt += 1
                    continue
                if downgrade_llm is not None and not downgrade_used:
                    active_llm = downgrade_llm
                    downgrade_used = True
                    attempt = 0
                    max_retries = 0
                    telemetry.downgraded = True
                    telemetry.model = _model_name(downgrade_llm)
                    continue
                telemetry.status = "failed"
                raise failures[-1]

    raise failures[-1] if failures else RuntimeError(f"{node_name} LLM invocation failed.")


def data_agent_node(state: BursaAgentState) -> Dict:
    node = "data_agent"
    ticker = state["ticker"]
    with telemetry_span(
        node,
        tool_calls=2,
        metadata={"ticker": ticker, "provider": "yfinance"},
    ) as telemetry:
        try:
            stock_info = fetch_bursa_stock_data.invoke({"ticker_code_or_name": ticker})
            company_name = stock_info.get("company_name") or stock_info.get("longName") or ticker
            if stock_info.get("status") == "FAILED":
                raise RuntimeError(stock_info.get("error", "Yahoo Finance fundamentals failed."))

            quarterly_info = fetch_bursa_quarterly_reports.invoke({"ticker_code_or_name": ticker})
            source = quarterly_info.get("source")
            status = quarterly_info.get("status")
            data_review = quarterly_info.get("data_quality_review") if isinstance(quarterly_info, dict) else None
            review_status = data_review.get("status") if isinstance(data_review, dict) else None
            tavily_evidence = data_review.get("tavily_evidence") if isinstance(data_review, dict) else None
            tavily_status = tavily_evidence.get("status") if isinstance(tavily_evidence, dict) else None
            telemetry.metadata["data_review_status"] = review_status or "N/A"
            telemetry.metadata["tavily_evidence_status"] = tavily_status or "N/A"
            fallback_used = bool(quarterly_info.get("fallback_used")) or source == "tavily_search"
            telemetry.fallback_used = fallback_used
            telemetry.fallback_provider = quarterly_info.get("fallback_provider")
            telemetry.fallback_reason = quarterly_info.get("fallback_reason")
            if fallback_used:
                telemetry.status = "fallback" if status == "FALLBACK_SUCCESS" else "failed"

            raw_data = {"fundamentals": stock_info, "quarterly_reports": quarterly_info}
            trace = [f"SUCCESS [data_agent]: Ingested fundamentals for {company_name} ({ticker})."]
            if source == "yfinance":
                trace.append("SUCCESS [data_agent]: Quarterly financials loaded via yfinance.")
                if review_status == "VERIFIED":
                    trace.append("SUCCESS [data_agent]: Quarterly financials double-checked against yfinance income statement.")
                elif review_status == "MISMATCH":
                    mismatch_count = len(data_review.get("mismatches") or []) if isinstance(data_review, dict) else 0
                    trace.append(
                        f"WARNING [data_agent]: Quarterly financial double-check found {mismatch_count} mismatch(es)."
                    )
                elif review_status == "WARNING":
                    warning_count = len(data_review.get("warnings") or []) if isinstance(data_review, dict) else 0
                    trace.append(
                        f"WARNING [data_agent]: Quarterly financial double-check produced {warning_count} warning(s)."
                    )
                elif review_status == "UNCHECKED":
                    trace.append("WARNING [data_agent]: Quarterly financials could not be double-checked.")
                if tavily_status == "SUCCESS":
                    result_count = len(tavily_evidence.get("results") or []) if isinstance(tavily_evidence, dict) else 0
                    trace.append(f"SUCCESS [data_agent]: Tavily found {result_count} external quarterly evidence result(s).")
                elif tavily_status:
                    trace.append(f"WARNING [data_agent]: Tavily external evidence search status {tavily_status}.")
            elif source == "tavily_search" and status == "FALLBACK_SUCCESS":
                trace.append(
                    "FALLBACK [data_agent]: YFinance quarterly data unavailable; Tavily fallback succeeded."
                )
            else:
                raise RuntimeError(quarterly_info.get("error", "Quarterly reports unavailable."))

            return {
                "raw_data": raw_data,
                "company_name": company_name,
                "workflow_status": "SUCCESS",
                "audit_trace": trace,
                "node_metrics": [telemetry.as_dict()],
            }
        except Exception as exc:
            return error_state(node, exc, telemetry)


def planner_agent_node(state: BursaAgentState) -> Dict:
    node = "planner_agent"
    if _has_failed(state):
        return failed_dependency_state(node, "Planning skipped because data ingestion failed.")
    with telemetry_span(node, llm_calls=0) as telemetry:
        plan = _build_research_plan(state).model_dump()
        telemetry.metadata["data_quality"] = plan["data_quality"]
        telemetry.metadata["planned_agents"] = len(plan["required_agents"])
        return {
            "research_plan": plan,
            "audit_trace": [
                "SUCCESS [planner_agent]: Built deterministic orchestration plan "
                f"with {plan['data_quality']} data quality."
            ],
            "node_metrics": [telemetry.as_dict()],
        }


def analysis_agent_node(state: BursaAgentState) -> Dict:
    node = "analysis_agent"
    if _has_failed(state):
        return failed_dependency_state(node, "Analysis skipped because data ingestion failed.")
    with telemetry_span(node, llm_calls=0) as telemetry:
        raw = state["raw_data"]
        metrics = _deterministic_analysis(raw).model_dump()
        return {
            "financial_metrics": metrics,
            "audit_trace": ["SUCCESS [analysis_agent]: Deterministically extracted financial metrics."],
            "node_metrics": [telemetry.as_dict()],
        }


def modeling_agent_node(state: BursaAgentState) -> Dict:
    node = "modeling_agent"
    if _has_failed(state):
        return failed_dependency_state(node, "Modeling skipped because data ingestion failed.")
    with telemetry_span(node, model=_model_name(heavy_llm), llm_calls=1, tool_calls=1) as telemetry:
        try:
            raw = state["raw_data"]
            fundamentals = raw.get("fundamentals", {}) if isinstance(raw, dict) else {}
            price = fundamentals.get("current_price")
            pe = fundamentals.get("pe_ratio")

            dcf = calculate_dcf_val.invoke({"current_price": price, "pe_ratio": pe})
            if dcf.get("status") == "FAILED":
                raise ValueError(dcf.get("error", "DCF calculation failed."))

            prompt = MODELING_AGENT_PROMPT.format(
                fewshot=FEWSHOT_STYLE_GUIDE,
                ticker=state["ticker"],
                current_price=price,
                pe_ratio=pe,
                dcf_output=dcf,
            )
            try:
                content, metric = _invoke_llm(node, heavy_llm, prompt, downgrade_llm=fast_llm)
                dcf["summary_notes"] = content
                metric["tool_calls"] = 1
            except Exception as llm_exc:
                fallback = _deterministic_modeling_summary(
                    ticker=state["ticker"],
                    current_price=price,
                    pe_ratio=pe,
                    dcf=dcf,
                    fallback_reason=str(llm_exc),
                )
                dcf["summary_notes"] = fallback.content
                dcf["summary_source"] = fallback.source
                dcf["summary_fallback_reason"] = fallback.fallback_reason
                metric = _mark_metric_fallback(
                    telemetry.as_dict(),
                    provider="deterministic_modeling_summary",
                    reason=str(llm_exc) or type(llm_exc).__name__,
                    error_type=type(llm_exc).__name__,
                )
            trace = [
                "SUCCESS [modeling_agent]: DCF Intrinsic Value calculated at MYR "
                f"{dcf['estimated_fair_value_myr']}."
            ]
            if dcf.get("summary_source") == "deterministic_fallback":
                trace.append("FALLBACK [modeling_agent]: LLM modeling summary unavailable; used DCF summary.")
            if dcf.get("pe_ratio_substituted"):
                trace.append("DEGRADED [modeling_agent]: Invalid P/E replaced with FBM KLCI baseline.")
            return {"valuation_model": dcf, "audit_trace": trace, "node_metrics": [metric]}
        except Exception as exc:
            return error_state(node, exc, telemetry)


def synthesis_agent_node(state: BursaAgentState) -> Dict:
    node = "synthesis_agent"
    if _has_failed(state):
        return failed_dependency_state(node, "Synthesis skipped because prerequisite agents failed.")
    with telemetry_span(node, model=_model_name(heavy_llm), llm_calls=1) as telemetry:
        prompt = SYNTHESIS_AGENT_PROMPT.format(
            fewshot=FEWSHOT_STYLE_GUIDE,
            ticker=state["ticker"],
            research_plan=_compact_research_plan(state.get("research_plan", {})),
            raw_data=_compact_raw_data(state["raw_data"]),
            financial_metrics=_compact_financial_metrics(state["financial_metrics"]),
            valuation_model=_compact_valuation_model(state["valuation_model"]),
        )
        try:
            content, metric = _invoke_llm(node, heavy_llm, prompt, downgrade_llm=fast_llm)
            return {
                "baseline_thesis": content,
                "audit_trace": ["SUCCESS [synthesis_agent]: Compiled baseline research dossier."],
                "node_metrics": [metric],
            }
        except Exception as exc:
            fallback = _deterministic_synthesis_summary(state, str(exc) or type(exc).__name__)
            metric = _mark_metric_fallback(
                telemetry.as_dict(),
                provider="deterministic_synthesis_summary",
                reason=fallback.fallback_reason or type(exc).__name__,
                error_type=type(exc).__name__,
            )
            return {
                "baseline_thesis": fallback.content,
                "audit_trace": ["FALLBACK [synthesis_agent]: LLM synthesis unavailable; used deterministic baseline dossier."],
                "node_metrics": [metric],
            }


def bull_agent_node(state: BursaAgentState) -> Dict:
    node = "bull_agent"
    if _has_failed(state):
        return failed_dependency_state(node, "Bull case skipped because prerequisite agents failed.")
    try:
        company_name = state.get("company_name", state.get("ticker", ""))
        raw = state.get("raw_data", {})
        compact_raw = _compact_raw_data(raw)
        fundamentals = raw.get("fundamentals", {}) if isinstance(raw, dict) else {}
        prompt = BULL_AGENT_PROMPT.format(
            fewshot=FEWSHOT_STYLE_GUIDE,
            company_name=company_name,
            ticker=state.get("ticker", ""),
            raw_data=compact_raw,
            current_price=fundamentals.get("current_price"),
            baseline_thesis=_compact_text(state.get("baseline_thesis", ""), 800),
        )
        content, metric = _invoke_llm(node, heavy_llm, prompt)
        rounds = state.get("debate_rounds", 0) + 1
        return {
            "bull_case": content,
            "debate_rounds": rounds,
            "audit_trace": [f"SUCCESS [bull_agent]: Formulated bullish case, round {rounds}."],
            "node_metrics": [metric],
        }
    except Exception as exc:
        fallback = _deterministic_debate_case(state, stance="bull", fallback_reason=str(exc))
        rounds = state.get("debate_rounds", 0) + 1
        return {
            "bull_case": fallback.content,
            "debate_rounds": rounds,
            "audit_trace": [
                "FALLBACK [bull_agent]: LLM debate case unavailable; "
                "used deterministic bullish case."
            ],
            "node_metrics": [_fallback_metric(node, exc)],
        }


def bear_agent_node(state: BursaAgentState) -> Dict:
    node = "bear_agent"
    if _has_failed(state):
        return failed_dependency_state(node, "Bear case skipped because prerequisite agents failed.")
    try:
        company_name = state.get("company_name", state.get("ticker", ""))
        raw = state.get("raw_data", {})
        compact_raw = _compact_raw_data(raw)
        fundamentals = raw.get("fundamentals", {}) if isinstance(raw, dict) else {}
        prompt = BEAR_AGENT_PROMPT.format(
            fewshot=FEWSHOT_STYLE_GUIDE,
            company_name=company_name,
            ticker=state.get("ticker", ""),
            raw_data=compact_raw,
            current_price=fundamentals.get("current_price"),
            baseline_thesis=_compact_text(state.get("baseline_thesis", ""), 800),
        )
        content, metric = _invoke_llm(node, heavy_llm, prompt)
        return {
            "bear_case": content,
            "audit_trace": ["SUCCESS [bear_agent]: Formulated risk counter-arguments."],
            "node_metrics": [metric],
        }
    except Exception as exc:
        fallback = _deterministic_debate_case(state, stance="bear", fallback_reason=str(exc))
        return {
            "bear_case": fallback.content,
            "audit_trace": [
                "FALLBACK [bear_agent]: LLM debate case unavailable; "
                "used deterministic bearish case."
            ],
            "node_metrics": [_fallback_metric(node, exc)],
        }


def debate_agent_node(state: BursaAgentState) -> Dict:
    node = "debate_agent"
    if _has_failed(state):
        return failed_dependency_state(node, "Debate skipped because prerequisite agents failed.")
    if not state.get("bull_case") or not state.get("bear_case"):
        return FailureState(
            failed_stage=node,
            error_message="Debate requires both Bull and Bear cases.",
            errors=[
                WorkflowError(
                    node=node,
                    type="MissingDebateInput",
                    message="Debate requires both Bull and Bear cases.",
                )
            ],
        ).model_dump()
    with telemetry_span(node, llm_calls=0) as telemetry:
        brief = _build_debate_brief(state).model_dump()
        telemetry.metadata["contested_points"] = len(brief["contested_points"])
        telemetry.metadata["decision_questions"] = len(brief["decision_questions"])
        return {
            "debate_brief": brief,
            "audit_trace": ["SUCCESS [debate_agent]: Structured bull/bear debate before judge."],
            "node_metrics": [telemetry.as_dict()],
        }


def replanner_agent_node(state: BursaAgentState) -> Dict:
    node = "replanner_agent"
    if _has_failed(state):
        return failed_dependency_state(node, "Replanning skipped because prerequisite agents failed.")
    with telemetry_span(node, llm_calls=0) as telemetry:
        decision = _build_replan_decision(state).model_dump()
        telemetry.metadata["triggered"] = decision["triggered"]
        telemetry.metadata["judge_can_continue"] = decision["judge_can_continue"]
        telemetry.metadata["data_quality"] = decision["data_quality"]

        if not decision["judge_can_continue"]:
            return FailureState(
                failed_stage=node,
                error_message="Replanner blocked judge_agent because debate inputs are incomplete.",
                errors=[
                    WorkflowError(
                        node=node,
                        type="ReplanBlocked",
                        message="Replanner blocked judge_agent because debate inputs are incomplete.",
                    )
                ],
                audit_trace=[
                    "FAILED [replanner_agent]: Debate inputs incomplete; judge_agent blocked."
                ],
            ).model_dump() | {"node_metrics": [telemetry.as_dict()]}

        previous_plan = dict(state.get("research_plan", {}) or {})
        plan = _build_research_plan(state).model_dump()
        plan["replanning_events"] = list(previous_plan.get("replanning_events", []))
        plan["recovery_actions"] = list(previous_plan.get("recovery_actions", []))
        if decision["triggered"]:
            plan["data_quality"] = decision["data_quality"]
            plan["replanning_events"] = [
                *plan["replanning_events"],
                *decision["replanning_events"],
            ]
            plan["recovery_actions"] = [
                *plan["recovery_actions"],
                *decision["recovery_actions"],
            ]
            trace = [
                "REPLANNED [replanner_agent]: Updated research plan after degraded node output."
            ]
        else:
            trace = ["SUCCESS [replanner_agent]: No replanning required."]

        return {
            "research_plan": plan,
            "replan_decision": decision,
            "audit_trace": trace,
            "node_metrics": [telemetry.as_dict()],
        }


def judge_agent_node(state: BursaAgentState) -> Dict:
    node = "judge_agent"
    if _has_failed(state):
        return failed_dependency_state(node, "Judge skipped because the adversarial debate is incomplete.")
    if not state.get("bull_case") or not state.get("bear_case"):
        return FailureState(
            failed_stage=node,
            error_message="Judge requires both Bull and Bear cases.",
            errors=[
                WorkflowError(
                    node=node,
                    type="MissingDebateInput",
                    message="Judge requires both Bull and Bear cases.",
                )
            ],
        ).model_dump()
    try:
        raw = state.get("raw_data", {})
        compact_raw = _compact_raw_data(raw)
        fundamentals = raw.get("fundamentals", {}) if isinstance(raw, dict) else {}
        prompt = JUDGE_AGENT_PROMPT.format(
            fewshot=FEWSHOT_STYLE_GUIDE,
            ticker=state.get("ticker", ""),
            company_name=state.get("company_name", state.get("ticker", "")),
            raw_data=compact_raw,
            valuation_model=_compact_valuation_model(state.get("valuation_model", {})),
            debate_brief=state.get("debate_brief", {}),
            bull_case=_compact_text(state.get("bull_case", "N/A"), 800),
            bear_case=_compact_text(state.get("bear_case", "N/A"), 800),
            current_price=fundamentals.get("current_price"),
        )
        content, metric = _invoke_llm(node, heavy_llm, prompt, downgrade_llm=fast_llm)
        val_model = state.get("valuation_model", {})
        dcf_target = val_model.get("estimated_fair_value_myr") if isinstance(val_model, dict) else None
        target_price = _extract_verdict_price(content, "Target Price") or dcf_target
        verdict = InvestmentVerdict(
            raw_judgement=_normalize_report_text(content),
            target_price_myr=target_price,
            valid=True,
        ).model_dump()
        return {
            "judge_verdict": verdict,
            "audit_trace": ["SUCCESS [judge_agent]: Rendered final arbitrated rating."],
            "node_metrics": [metric],
        }
    except Exception as exc:
        with telemetry_span(node, model=_model_name(heavy_llm), llm_calls=1) as telemetry:
            return error_state(node, exc, telemetry)


def report_agent_node(state: BursaAgentState) -> Dict:
    node = "report_agent"
    metrics = summarize_telemetry(list(state.get("node_metrics", [])))
    if _has_failed(state) or not state.get("judge_verdict", {}).get("valid"):
        stage = state.get("failed_stage") or "report_agent"
        reason = state.get("error_message") or "No valid Investment Committee verdict was generated."
        report = (
            "# Analysis could not be completed\n\n"
            f"Failed stage: {stage}\n\n"
            f"Reason: {reason}\n\n"
            "No investment recommendation was generated."
        )
        return {
            "final_report": report,
            "workflow_status": "FAILED",
            "workflow_metrics": metrics,
            "audit_trace": [f"INFO [report_agent]: Generated failure report for {stage}."],
        }

    raw = state["raw_data"]
    fundamentals = raw.get("fundamentals", {}) if isinstance(raw, dict) else {}
    quarterly = raw.get("quarterly_reports", {}) if isinstance(raw, dict) else {}
    verdict = state.get("judge_verdict", {})
    raw_judgement = _normalize_report_text(verdict.get("raw_judgement", "N/A"))
    company_name = state.get("company_name") or fundamentals.get("company_name", "N/A")
    symbol = fundamentals.get("symbol", state.get("ticker"))
    sector = fundamentals.get("sector", "N/A")
    industry = fundamentals.get("industry", "N/A")
    price = fundamentals.get("current_price")
    judge_target = _extract_verdict_price(raw_judgement, "Target Price")
    valuation = state.get("valuation_model", {}) if isinstance(state.get("valuation_model", {}), dict) else {}
    dcf_fair_value = valuation.get("estimated_fair_value_myr")
    report_target = judge_target or verdict.get("target_price_myr") or dcf_fair_value
    upside = None
    price_number = _safe_float(price)
    target_number = _safe_float(report_target)
    if price_number and target_number:
        upside = ((target_number - price_number) / price_number) * 100
    pe = fundamentals.get("pe_ratio")
    div = fundamentals.get("dividend_yield")
    div_number = _safe_float(div)
    if div_number is not None and 0 < abs(div_number) < 1:
        div_number *= 100
    total_return = (upside or 0) + (div_number or 0) if upside is not None else None
    low = fundamentals.get("fifty_two_week_low", "N/A")
    high = fundamentals.get("fifty_two_week_high", "N/A")
    source_note = quarterly.get("source", "N/A")
    quarterly_status = quarterly.get("status", "N/A")
    fallback_note = quarterly.get("fallback_reason") if quarterly.get("fallback_used") else None
    recommendation = _extract_verdict_line(raw_judgement, "Recommendation")
    confidence = _extract_verdict_line(raw_judgement, "Confidence")
    entry_price_number = _extract_verdict_price(raw_judgement, "Entry Price") or price
    stop_loss_number = _extract_verdict_price(raw_judgement, "Stop-Loss")
    if stop_loss_number is None:
        stop_loss_number = _derive_stop_loss(recommendation, entry_price_number, low)
    entry_price = f"MYR {_format_price(entry_price_number)}" if _safe_float(entry_price_number) is not None else "N/A"
    stop_loss = f"MYR {_format_price(stop_loss_number)}" if stop_loss_number is not None else "N/A"
    company_description = _compact_text(_normalize_report_text(fundamentals.get("summary")), 450)
    if not company_description:
        company_description = f"{company_name} is classified under {sector} / {industry}."
    quarterly_actuals_table = _quarterly_table_markdown(quarterly.get("quarterly_financials"))
    financial_forecast_table = _financial_forecast_markdown(quarterly.get("quarterly_financials"))
    valuation_caveat = valuation.get("pe_ratio_substitution_reason") or "DCF fair value is generated from available price and P/E inputs."
    research_plan = state.get("research_plan", {}) if isinstance(state.get("research_plan", {}), dict) else {}
    debate_brief = state.get("debate_brief", {}) if isinstance(state.get("debate_brief", {}), dict) else {}
    plan_gaps = research_plan.get("data_gaps") or []
    plan_gap_text = "; ".join(plan_gaps) if plan_gaps else "No major orchestration data gaps flagged."
    replanning_events = research_plan.get("replanning_events") or []
    recovery_actions = research_plan.get("recovery_actions") or []
    replanning_text = "; ".join(replanning_events) if replanning_events else "No replanning triggered."
    recovery_text = "; ".join(recovery_actions) if recovery_actions else "No recovery action required."
    contested_points = debate_brief.get("contested_points") or []
    decision_questions = debate_brief.get("decision_questions") or []
    contested_text = "\n".join(f"- {point}" for point in contested_points) or "N/A"
    decision_questions_text = "\n".join(f"- {question}" for question in decision_questions) or "N/A"
    thesis_text = _clean_report_section(
        state.get("baseline_thesis"),
        _deterministic_report_thesis(company_name, recommendation, price, report_target, pe, div, sector),
        min_chars=140,
    )
    bull_text = _clean_report_section(
        state.get("bull_case"),
        _deterministic_bull_summary(company_name, report_target, price, div),
        min_chars=120,
    )
    bear_text = _clean_report_section(
        state.get("bear_case"),
        _deterministic_bear_summary(company_name, low),
        min_chars=120,
    )
    verdict_text = _clean_report_section(raw_judgement, raw_judgement, min_chars=40)
    report_title = (
        f"{recommendation or 'Investment'} call: income support, but evidence quality controls upside"
        if recommendation == "HOLD"
        else f"{recommendation or 'Investment'} call: valuation and risk balanced against latest fundamentals"
    )

    report = f"""# BURSA MALAYSIA INVESTMENT RESEARCH REPORT
**Bursa Malaysia Equity Research | Company Update**  
**Date:** {date.today().isoformat()}  
**Company:** {company_name} ({symbol})

---

## Investment Call

| Rating | Target Price | Current Price | Capital Upside | Dividend Yield | Expected Total Return |
| --- | --- | --- | --- | --- | --- |
| {recommendation} | RM {_format_price(report_target, 2)} | RM {_format_price(price)} | {_format_percent(upside)} | {_format_percent(div)} | {_format_percent(total_return)} |

**Entry Price:** {entry_price}  
**Stop-Loss:** {stop_loss}  
**Confidence:** {confidence}

## {report_title}

{_deterministic_report_thesis(company_name, recommendation, price, report_target, pe, div, sector)}

## Company Snapshot

| Item | Detail |
| --- | --- |
| Sector coverage | {sector} |
| Industry | {industry} |
| Company description | {company_description} |
| Market capitalisation | {_format_market_cap(fundamentals.get('market_cap'))} |
| 52-week range | RM {_format_price(low)} - RM {_format_price(high)} |
| P/E ratio | {_format_number(pe)}x |
| Forward P/E | {_format_number(fundamentals.get('forward_pe'))}x |

---

## Investment View

### Thesis
{thesis_text}

### Valuation
The committee target price is RM {_format_price(report_target, 2)}, versus the current price of RM {_format_price(price)}. This implies capital upside/downside of {_format_percent(upside)} before dividends. DCF fair value reference: RM {_format_price(dcf_fair_value, 2)}. {valuation_caveat}

---

## Debate Summary

### Bull Case
{bull_text}

### Bear Case
{bear_text}

### Debate Before Judge
**Contested points**
{contested_text}

**Decision questions**
{decision_questions_text}

### Investment Committee Verdict
{verdict_text}

---

## Recent Quarterly Actuals

{quarterly_actuals_table}

## Financial Forecast

{financial_forecast_table}

## Data Quality

| Source | Status | Note |
| --- | --- | --- |
| Orchestration plan | {research_plan.get('data_quality', 'N/A')} | {plan_gap_text} |
| Fundamentals | {fundamentals.get('source', 'N/A')} | {fundamentals.get('status', 'N/A')} |
| Quarterly financials | {source_note} | {_join_available([str(quarterly_status), str(fallback_note or '')])} |
| Valuation model | {valuation.get('status', 'N/A')} | P/E used: {_format_number(valuation.get('pe_ratio_used'))}x |

## Replanning Notes

**Planner events:** {replanning_text}  
**Recovery action:** {recovery_text}

## Rating Guide

BUY: expected positive absolute return over the next 12 months.  
HOLD: expected range-bound return or insufficient evidence for a directional call.  
SELL: expected negative absolute return or material downside risk.

## Disclaimer

This report is generated by an agentic AI research workflow using available market and company data. It is for research and education only and is not personal financial advice. Investors should verify source documents and consider their own objectives, risk tolerance, and constraints before making investment decisions.

---
*Generated by Agentic AI System for Bursa Malaysia Equities*
"""
    report = _normalize_report_text(report)
    return {
        "final_report": report,
        "workflow_status": "SUCCESS",
        "workflow_metrics": metrics,
        "audit_trace": ["SUCCESS [report_agent]: Generated investment research report."],
    }
