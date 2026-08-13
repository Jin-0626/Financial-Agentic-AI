---
name: company-research
description: Research a Bursa Malaysia company using company filings, financial data, market context, and primary-source evidence.
---

# Company Research Workflow

Use this skill when the user requests company research.

## Workflow

1. Resolve the company and Bursa ticker.

2. Obtain a research snapshot using:
   `build_bursa_research_snapshot`

3. Retrieve recent official filings when material:
   `search_official_bursa_filings`

4. Retrieve quarterly reports when financial trends matter:
   `fetch_bursa_quarterly_reports`

5. Use market context only after primary company information
   has been established.

6. Identify:
   - latest material developments
   - financial trend
   - business drivers
   - risks
   - catalysts
   - evidence gaps

7. If valuation is requested or decision-relevant,
   invoke the valuation workflow.

## Research Priority

Prefer primary filings over news interpretation.

Avoid collecting information without connecting it to the investment thesis.