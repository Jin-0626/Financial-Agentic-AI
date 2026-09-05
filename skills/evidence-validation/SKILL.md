---
name: evidence-validation
description: Validate Bursa research drafts against supplied evidence, catching unsupported figures, period mistakes, source clutter, and overconfident recommendations.
license: MIT
---

# Evidence Validation

Use this skill before presenting a final Bursa equity briefing or structured
research output.

## Checks

1. Verify company identity, stock code, and reporting period.
2. Check every exact figure against supplied evidence.
3. Flag unsupported target prices, valuation ranges, dividends, ratios, lease
   liabilities, cash-flow figures, and balance-sheet numbers.
4. Flag period-basis mistakes, especially QoQ claims based on cumulative
   interim-period values.
5. Remove raw source IDs, chunk IDs, URLs, RAG labels, hashes, JSON evidence
   payloads, and citation columns from the user-facing report.
6. Require correction when news snippets are converted into precise valuation
   claims without a quantitative source.

## Result

Return a concise validation decision with warnings, unsupported claims, and
required corrections. A draft fails validation if it contains unsupported
numbers or confusing period labels.
