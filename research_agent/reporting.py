import re

DISCLAIMER = "This research is for education only and is not personal financial advice."
MIN_PIPE_BLOB_SEPARATORS = 10
MIN_PIPE_BLOB_CELLS = 6
MIN_REPAIRED_TABLE_ROWS = 2
FINANCIAL_NOTE = "*All figures are in Malaysian Ringgit (RM) unless noted otherwise.*"
REPORT_SECTION_TITLES = {
    "1": "Executive Summary",
    "2": "Financial Statements, Key Ratios, Historical Performance",
    "3": "Sector Insight, Forecast Explanation, Valuation, Risks",
    "4": "Final Investment View",
}


def clean_visible_report(markdown: str) -> str:
    """Keep the user-facing report clean while preserving the investment view."""
    report = markdown.strip()
    report = _remove_source_and_quality_sections(report)
    report = _remove_source_markers(report)
    report = _normalize_report_structure(report)
    report = _repair_financial_pipe_blobs(report)
    report = _remove_unsupported_metric_lines(report)
    report = _normalize_report_structure(report)
    report = _ensure_target_price_in_summary(report)
    report = _normalize_disclaimer(report)
    return report.strip()


def enforce_financial_statement_table(markdown: str, table_markdown: str) -> str:
    """Replace the visible financial section with a deterministic table."""
    if not table_markdown.strip():
        return markdown

    markdown = _normalize_report_structure(markdown)
    replacement_body = f"\n\n{table_markdown.strip()}\n\n{FINANCIAL_NOTE}\n\n"
    section_pattern = re.compile(
        r"(?ims)(^#{2,3}\s*(?:\d+\.\s*)?Financial Statements[^\n]*\n).*?(?=^#{2,3}\s*(?:\d+\.\s*)?\S|\Z)"
    )
    match = section_pattern.search(markdown)
    if match:
        return f"{markdown[: match.end(1)]}{replacement_body}{markdown[match.end() :]}"

    summary_pattern = re.compile(r"(?ims)(^#{2,3}\s*(?:\d+\.\s*)?Executive Summary[^\n]*\n.*?)(?=^#{2,3}\s|\Z)")
    summary_match = summary_pattern.search(markdown)
    section = f"## 2. Financial Statements, Key Ratios, Historical Performance{replacement_body}"
    if summary_match:
        return f"{markdown[: summary_match.end()]}{section}\n{markdown[summary_match.end() :]}"
    return f"{section}\n{markdown}"


def _normalize_report_structure(markdown: str) -> str:
    markdown = _strip_preamble_title(markdown)
    lines = []
    current_section = ""
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if re.fullmatch(r"-{3,}", line):
            continue
        normalized_heading = _normalize_section_heading(line)
        if normalized_heading:
            current_section = normalized_heading.removeprefix("## ").split(".", 1)[0]
            lines.append(normalized_heading)
            continue
        if current_section == "3":
            normalized_label = _normalize_section_three_label(line)
            if normalized_label:
                lines.append(normalized_label)
                continue
        lines.append(raw_line.rstrip())
    normalized = "\n".join(lines)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _strip_preamble_title(markdown: str) -> str:
    section_match = re.search(r"(?im)^(?:#{1,6}\s*)?(?:\*\*)?\s*1\.\s*Executive Summary", markdown)
    if not section_match:
        return markdown
    return markdown[section_match.start() :]


def _normalize_section_heading(line: str) -> str:
    clean = line.strip().strip("*_ ")
    clean = re.sub(r"^#{1,6}\s*", "", clean).strip()
    match = re.match(r"^(?P<number>[1-4])\.\s*(?P<title>.+?)\s*:?\s*$", clean, flags=re.IGNORECASE)
    if not match:
        return ""

    number = match.group("number")
    title = match.group("title").lower()
    if number == "1" and "summary" in title:
        return f"## {number}. {REPORT_SECTION_TITLES[number]}"
    if number == "2" and ("financial" in title or "ratio" in title or "historical" in title):
        return f"## {number}. {REPORT_SECTION_TITLES[number]}"
    if number == "3" and any(token in title for token in ("sector", "forecast", "valuation", "risk")):
        return f"## {number}. {REPORT_SECTION_TITLES[number]}"
    if number == "4" and ("final" in title or "investment" in title or "view" in title):
        return f"## {number}. {REPORT_SECTION_TITLES[number]}"
    return ""


def _normalize_section_three_label(line: str) -> str:
    if line.lstrip().startswith(("-", "* ")) or "|" in line:
        return ""
    clean = line.strip().strip("*_ ")
    clean = re.sub(r"^#{1,6}\s*", "", clean).strip()
    if not clean:
        return ""
    if re.search(r":\s*\S", clean) and not clean.endswith(":"):
        return ""

    label_map = (
        (("sector", "industry"), "Sector Insight"),
        (("forecast", "rationale"), "Forecast Explanation"),
        (("valuation", "target", "multiple"), "Valuation"),
        (("risk", "watch"), "Risks"),
    )
    lowered = clean.lower()
    for keywords, label in label_map:
        if any(keyword in lowered for keyword in keywords) and len(clean) <= 80:
            return f"**{label}:**"
    return ""


def _repair_financial_pipe_blobs(markdown: str) -> str:
    lines = []
    for line in markdown.splitlines():
        if line.count("|") < MIN_PIPE_BLOB_SEPARATORS:
            lines.append(line)
            continue

        repaired = _pipe_blob_to_table(line)
        lines.extend(repaired.splitlines() if repaired else [line])
    return "\n".join(lines)


def _pipe_blob_to_table(line: str) -> str:
    cells = [cell.strip() for cell in line.split("|") if cell.strip()]
    cells = [cell for cell in cells if not re.fullmatch(r"[-:\s]+", cell)]
    if len(cells) < MIN_PIPE_BLOB_CELLS:
        return ""

    rows: list[list[str]] = []
    current: list[str] = []
    for cell in cells:
        if _looks_like_metric_label(cell):
            if len(current) > 1:
                rows.append(current)
            current = [cell]
        elif current:
            current.append(cell)
    if len(current) > 1:
        rows.append(current)

    if len(rows) < MIN_REPAIRED_TABLE_ROWS:
        return ""

    max_values = max(len(row) - 1 for row in rows)
    headers = _financial_table_headers(max_values)
    markdown_rows = [
        f"| {' | '.join(headers)} |",
        f"| {' | '.join(['---'] * len(headers))} |",
    ]
    for row in rows:
        padded = [*row, *(["N/A"] * (len(headers) - len(row)))]
        markdown_rows.append(f"| {' | '.join(padded[: len(headers)])} |")
    return "\n".join(markdown_rows)


def _looks_like_metric_label(cell: str) -> bool:
    if not re.search(r"[A-Za-z]", cell):
        return False
    value_pattern = r"[-+]?(?:RM\s*)?\d[\d,]*(?:\.\d+)?\s*(?:%|x|m|mn|bn|b)?"
    return not re.fullmatch(value_pattern, cell, flags=re.IGNORECASE)


def _financial_table_headers(value_count: int) -> list[str]:
    if value_count == 2:
        return ["Metric (FY 2025-FY 2026)", "2025-12-31", "2026-03-31"]
    if value_count == 3:
        return ["Metric", "Latest", "Previous", "Prior"]
    if value_count == 4:
        return ["Metric (Last 4Q)", "Latest Q", "Previous Q", "Q-2", "Q-3"]
    return ["Metric", *[f"Value {idx}" for idx in range(1, value_count + 1)]]


def _remove_source_and_quality_sections(markdown: str) -> str:
    headings = (
        r"(?:key\s+)?sources?",
        r"data\s*quality",
        r"citations?",
        r"source\s+list",
    )
    pattern = re.compile(rf"(?ims)^##+\s*(?:\d+\.\s*)?(?:{'|'.join(headings)}).*?(?=^##+\s|\Z)")
    cleaned = pattern.sub("", markdown)
    return re.sub(r"(?im)^\s*\*\*data\s*quality warnings?\*\*.*(?:\n(?!##).*)*", "", cleaned)


def _remove_source_markers(markdown: str) -> str:
    cleaned = re.sub(r"【\s*source:[^】]+】", "", markdown, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+\|\s*Source\s*\|.*?(?=\n\n|\Z)", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"(?im)^\s*\|\s*Metric\s*\|\s*Value\s*\|\s*Source\s*\|.*?(?=\n\n|\Z)", "", cleaned)
    cleaned = re.sub(r"(?im)^\s*[-*]\s*\*\*Sources?:\*\*.*$", "", cleaned)
    return re.sub(r"(?im)^\s*Sources?:\s*.*$", "", cleaned)


def _remove_unsupported_metric_lines(markdown: str) -> str:
    unsupported_patterns = (
        r"ROE",
        r"Debt\s*[- ]?\s*to\s*[- ]?\s*Equity",
        r"net\s*[- ]?\s*debt\s*[- ]?\s*to\s*[- ]?\s*equity",
        r"debt\s*[- ]?\s*to\s*[- ]?\s*EBITDA",
        r"Current Ratio",
        r"current\s+assets\s*/\s*current\s+liabilities",
        r"one[- ]?off gain",
        r"benchmark",
        r"KLSE Composite",
        r"consensus",
        r"sector average",
        r"Bursa Healthcare average",
        r"\bYoY\b",
        r"\bFY[-\s]?\d{4}\b",
        r"payout ratio",
        r"same[- ]store",
        r"management\s+has\s+signalled",
        r"regional peers",
        r"relative to peers",
        r"well below .*sector",
        r"lower end of .*sector",
    )
    pattern = re.compile("|".join(unsupported_patterns), flags=re.IGNORECASE)
    lines = []
    for line in markdown.splitlines():
        if _is_markdown_table_header(line):
            lines.append(line)
            continue
        if pattern.search(line):
            continue
        lines.append(line)
    return "\n".join(lines)


def _is_markdown_table_header(line: str) -> bool:
    cells = [cell.strip().lower() for cell in line.split("|") if cell.strip()]
    if not cells:
        return False
    return cells[0].startswith("metric") or cells[0] in {"ratio", "item"}


def _ensure_target_price_in_summary(markdown: str) -> str:
    if re.search(r"(?i)target\s+price", markdown):
        return markdown

    fair_value_match = re.search(
        r"(?i)(?:DCF\s+fair\s+value|fair\s+value)\s*[:=]?\s*(RM\s*[0-9]+(?:\.[0-9]+)?)",
        markdown,
    )
    if not fair_value_match:
        return markdown

    target_line = f"- **Target price:** {fair_value_match.group(1)}"
    rating_match = re.search(r"(?im)^(\*\*Rating:\*\*.*)$", markdown)
    if rating_match:
        insert_at = rating_match.end()
        return f"{markdown[:insert_at]}\n{target_line}{markdown[insert_at:]}"

    summary_match = re.search(r"(?im)^##+\s*(?:\d+\.\s*)?Executive Summary.*$", markdown)
    if summary_match:
        insert_at = summary_match.end()
        return f"{markdown[:insert_at]}\n{target_line}{markdown[insert_at:]}"

    return f"{target_line}\n\n{markdown}"


def _normalize_disclaimer(markdown: str) -> str:
    without_disclaimer = re.sub(
        r"(?im)^[-*_>\s]*(?:this\s+research\s+is\s+for\s+education\s+only\s+and\s+is\s+not\s+personal\s+financial\s+advice\.?)[-*_>\s]*$",
        "",
        markdown,
    ).strip()
    without_disclaimer = re.sub(r"\n{3,}", "\n\n", without_disclaimer)
    return f"{without_disclaimer}\n\n---\n\n{DISCLAIMER}"
