# Curated Financial Deep Agents providers

The five active command interfaces are `bnm_data.py`, `fred_data.py`,
`worldbank_data.py`, `treasury_data.py` and `sec_data.py`. They now delegate to
independently implemented bounded API clients in `financial_providers.py`.
Supported commands and examples are in `/scripts/AGENTS.md`.

FRED requires `FRED_API_KEY`. SEC requires `SEC_USER_AGENT` containing the application
name and contact email, including metadata commands. All clients use requests with
certificate verification, explicit connection/read timeouts and an 8 MiB response
limit. The outer provider worker limits stdout/stderr to 64 KiB each; request narrow
windows and small limits. Large SEC fact collections may exceed that outer limit.

The manifest distinguishes these registered clients from retained reference scripts.
Before execution, the worker verifies both the selected entry and shared client
hashes. Unregistered scripts and arbitrary URLs cannot execute through `run_script`.
Retained upstream-derived reference content retains its applicable licensing;
independent runtime clients do not assert authorship of that reference library.

The read-only script workspace supports discovery through the Deep Agents filesystem.
Role policy, workspace evidence receipts and built-in Traces cover provider calls.
There are four execution slots, a 60-second deadline and process cleanup on failure.
Provider failures remain explicit. Live endpoint coverage has not been verified by
the metadata-only and mocked regression checks.

Official API documentation:
- Bank Negara Malaysia: https://api.bnm.gov.my/
- FRED: https://fred.stlouisfed.org/docs/api/fred/
- World Bank: https://datahelpdesk.worldbank.org/knowledgebase/articles/889392
- FiscalData: https://fiscaldata.treasury.gov/api-documentation/
- SEC: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
