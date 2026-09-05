---
name: bursa-filing-analysis
description: Extract filing-backed Bursa Malaysia quarterly facts, distinguish cumulative interim periods from current-quarter values, and prepare evidence for a financial research report.
license: MIT
---

# Bursa Filing Analysis

Use this skill when analyzing Bursa quarterly filings, interim reports, Part A,
Part B, balance sheets, cash flow statements, dividends, borrowings, leases, or
segment results.

## Workflow

1. Resolve the Bursa company identity before extracting financial facts.
2. Retrieve filing excerpts with the filing RAG tool before writing findings.
3. Classify each exact figure by period basis:
   - `current_quarter` only when explicitly disclosed as current-quarter.
   - `cumulative_period` for interim financial-period totals.
   - `point_in_time` for balance sheet values and market snapshots.
   - `unknown` when the excerpt is unclear.
4. Extract evidence as compact claims with value, period, source type, and
   confidence.
5. Put absent metrics in missing fields instead of estimating them.
6. Avoid QoQ language unless both comparable values are explicitly
   current-quarter values.

## Output Rules

- Keep internal evidence structured and source-aware.
- Do not expose raw chunk IDs, hashes, JSON blobs, URLs, or citation columns in
  the final user-facing report.
- When evidence is thin, recommend `HOLD` or explain why a stronger call is not
  supportable.
