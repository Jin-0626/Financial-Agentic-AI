from research_agent.reporting import DISCLAIMER, clean_visible_report, enforce_financial_statement_table


def test_clean_report_removes_sources_and_keeps_disclaimer_at_end():
    raw = """## Executive Summary
**Rating:** **Hold**
- DCF fair value: RM1.66【source:calculate_dcf_valuation】

## Key Sources & Data Quality
| Source | Type | Confidence |
|---|---|---|
| yfinance | aggregator | medium |

## Final Investment View
Keep holding.

*This research is for education only and is not personal financial advice.*
"""

    cleaned = clean_visible_report(raw)

    assert "Key Sources" not in cleaned
    assert "Data Quality" not in cleaned
    assert "source:" not in cleaned
    assert "Target price" in cleaned
    assert cleaned.endswith(DISCLAIMER)


def test_clean_report_removes_common_unsupported_metrics():
    raw = """## Executive Summary
**Rating:** **Hold**
- DCF fair value: RM1.44

## Financial Statements
| Ratio | Value |
|---|---|
| ROE | ~8% |
| Debt-to-Equity | ~1.2x |
| Current Ratio | 0.9x |

The stock underperformed the KLSE Composite and had a one-off gain.
"""

    cleaned = clean_visible_report(raw)

    assert "ROE" not in cleaned
    assert "Debt-to-Equity" not in cleaned
    assert "Current Ratio" not in cleaned
    assert "one-off gain" not in cleaned
    assert "KLSE Composite" not in cleaned


def test_clean_report_removes_unsupported_derived_claims():
    raw = """## Executive Summary
**Rating:** **Buy**
- DCF fair value: RM0.73

## Financial Statements
Revenue fell 15% YoY after FY-2025 revenue of RM311m.
The current ratio is 1.86 and net-debt-to-equity ratio is 0.10.
Management has signalled plans for new outlets and same-store sales growth.
The valuation is well below the Healthcare sector and regional peers.

## Final Investment View
Buy.
"""

    cleaned = clean_visible_report(raw)

    assert "YoY" not in cleaned
    assert "FY-2025" not in cleaned
    assert "current ratio" not in cleaned
    assert "net-debt-to-equity" not in cleaned
    assert "Management has signalled" not in cleaned
    assert "same-store" not in cleaned
    assert "regional peers" not in cleaned


def test_clean_report_preserves_financial_markdown_tables():
    raw = """## Financial Statements, Key Ratios, Historical Performance
| Metric | Latest |
|---|---|
| Revenue | RM77.27m |
| Target price | RM0.73 |

**Latest Valuation Ratios**
| Metric | Latest |
|---|---|
| P/E (trailing) | 10x |
| Enterprise Value | RM 100m |

## Final Investment View
Buy.
"""

    cleaned = clean_visible_report(raw)

    assert "| Metric | Latest |" in cleaned
    assert "| Revenue | RM77.27m |" in cleaned
    assert "| Target price | RM0.73 |" in cleaned
    assert "**Latest Valuation Ratios**" in cleaned
    assert "| Enterprise Value | RM 100m |" in cleaned


def test_clean_report_repairs_single_line_financial_table_blob():
    pipe_blob = (
        "|-------|-------|-------|-------| Revenue (RM m) | 5,950.22 | 920.81 | 803.49 | "
        "EBITDA (RM m) | 720.93 | 185.25 | 115.28 | Operating income (RM m) | 375.45 | 65.65 | "
        "35.13 | Net income (RM m) | -154.88 | 78.57 | 27.75 | Diluted EPS (RM) | -0.046 | 0.176 | "
        "0.062 | P/E (trailing) | 4.39 |"
    )
    raw = f"""## Financial Statements, Key Ratios & Historical Performance
{pipe_blob}
"""

    cleaned = clean_visible_report(raw)

    assert "| Metric | Latest | Previous | Prior |" in cleaned
    assert "| Revenue (RM m) | 5,950.22 | 920.81 | 803.49 |" in cleaned
    assert "| Net income (RM m) | -154.88 | 78.57 | 27.75 |" in cleaned
    assert "| P/E (trailing) | 4.39 | N/A | N/A |" in cleaned


def test_clean_report_repairs_separator_prefixed_financial_table_blob():
    pipe_blob = (
        "|------------------|-----------|-----------|| Revenue (RM m) | 3,720 | 3,650 | Net Income (RM m) | "
        "1,876 | 1,752 | Diluted EPS (RM) | 0.0972 | 0.0907 | Cash (RM m) | 21,270 | 22,330 | "
        "Total Assets (RM m) | 561,651 | 570,313 | Total Debt (RM m) | 13,556 | 15,217 | "
        "Shareholders' Equity (RM m) | 59,938 | 59,138 | Operating Cash Flow (RM m) | 1,733 | 1,538 | "
        "Free Cash Flow (RM m) | 1,592 | 1,472 | P/E (trailing) | 14.03x | 14.03x | "
        "Forward P/E | 12.76x | 12.76x | Price-to-Book | 1.69x | 1.69x | Price-to-Sales | 6.85x | "
        "6.85x | Dividend Yield | 4.34% | 4.34% | Market Capitalisation | RM 100.74 bn | "
        "RM 100.74 bn | Enterprise Value | RM 107.34 bn | RM 107.34 bn | Book Value per Share | "
        "RM 3.062 | RM 3.062 |"
    )
    raw = f"""## 2. Financial Statements, Key Ratios, Historical Performance
{pipe_blob}

*All figures are in Malaysian Ringgit (RM) unless noted otherwise.*
"""

    cleaned = clean_visible_report(raw)

    assert "| Metric (FY 2025-FY 2026) | 2025-12-31 | 2026-03-31 |" in cleaned
    assert "| Revenue (RM m) | 3,720 | 3,650 |" in cleaned
    assert "| Shareholders' Equity (RM m) | 59,938 | 59,138 |" in cleaned
    assert "| Market Capitalisation | RM 100.74 bn | RM 100.74 bn |" in cleaned
    assert "|------------------|-----------|-----------|" not in cleaned


def test_clean_report_repairs_four_quarter_financial_table_blob():
    pipe_blob = (
        "| Revenue (RM m) | 400 | 300 | 200 | 100 | Net Income (RM m) | 40 | 30 | 20 | 10 | "
        "Diluted EPS (RM) | 0.04 | 0.03 | 0.02 | 0.01 |"
    )
    raw = f"""## 2. Financial Statements, Key Ratios, Historical Performance
{pipe_blob}
"""

    cleaned = clean_visible_report(raw)

    assert "| Metric (Last 4Q) | Latest Q | Previous Q | Q-2 | Q-3 |" in cleaned
    assert "| Revenue (RM m) | 400 | 300 | 200 | 100 |" in cleaned
    assert "| Net Income (RM m) | 40 | 30 | 20 | 10 |" in cleaned


def test_enforce_financial_statement_table_replaces_llm_financial_section():
    raw = """**1. Executive Summary**
Hold.

---

**2. Financial Statements, Key Ratios & Historical Performance**
| Metric | Latest | Previous |
| --- | --- | --- |
| Revenue | 300 | 200 |

**3. Valuation**
Target price RM0.24.
"""
    required_table = """| Metric (Last 4Q) | 2026-04-30 | 2026-01-31 | 2025-10-31 | Q-3 |
| --- | --- | --- | --- | --- |
| Revenue (RMm) | 225.97 | 238.44 | 229.66 | N/A |

**Latest Valuation Ratios**
| Metric | Latest |
| --- | --- |
| P/E (trailing) | 10x |
| Enterprise Value | RM 100m |"""

    cleaned = enforce_financial_statement_table(raw, required_table)

    assert "**1. Executive Summary**" not in cleaned
    assert "## 1. Executive Summary" in cleaned
    assert "## 2. Financial Statements, Key Ratios, Historical Performance" in cleaned
    assert required_table in cleaned
    assert "**Latest Valuation Ratios**" in cleaned
    assert "| Revenue | 300 | 200 |" not in cleaned
    assert "## 3. Sector Insight, Forecast Explanation, Valuation, Risks" in cleaned


def test_clean_report_normalizes_final_report_structure():
    raw = """**Mynews Holdings Berhad (5275.KL) - Bursa Malaysia Equity Research Report**

---

### 1. Executive Summary
- **Rating:** Buy
- **Current Price:** RM0.51
- **Target Price:** RM0.73
- **Upside:** 43%

---

### 2. Financial Statements, Key Ratios & Historical Performance
| Metric | Latest |
| --- | --- |
| Revenue | 77.27 |

### 3. Sector Insight, Forecast Explanation, Valuation & Risks
**Sector & Industry Context**
Consumer retail exposure.

*Forecast*
Target price is based on returned valuation assumptions.

**Valuation Summary**
Target price RM0.73.

**Risks**
Risks are qualitative.

---

**4. Final Investment View**
Buy.
"""

    cleaned = clean_visible_report(raw)

    assert "## 1. Executive Summary" in cleaned
    assert "## 2. Financial Statements, Key Ratios, Historical Performance" in cleaned
    assert "## 3. Sector Insight, Forecast Explanation, Valuation, Risks" in cleaned
    assert "## 4. Final Investment View" in cleaned
    assert "Bursa Malaysia Equity Research Report" not in cleaned
    assert "**1. Executive Summary**" not in cleaned
    assert "- **Target Price:** RM0.73" in cleaned
    assert "**Sector Insight:**" in cleaned
    assert "**Forecast Explanation:**" in cleaned
    assert "**Valuation:**" in cleaned
    assert "**Risks:**" in cleaned
    assert cleaned.count("\n---\n") == 1


def test_clean_report_handles_empty_body():
    cleaned = clean_visible_report("   ")

    assert cleaned.endswith(DISCLAIMER)


def test_clean_report_deduplicates_disclaimer():
    raw = f"""## Final Investment View
Hold.

{DISCLAIMER}

{DISCLAIMER}
"""

    cleaned = clean_visible_report(raw)

    assert cleaned.count(DISCLAIMER) == 1
