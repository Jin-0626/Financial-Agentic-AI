# Curated Financial financial data scripts

Five curated provider scripts adapted for Financial Deep Agents. The application exposes these files read-only through /scripts/ and registers a
curated run_script tool. Read /scripts/AGENTS.md for supported commands and limits.

| Script | Adds | Key | Integration priority |
| --- | --- | --- | --- |
| bnm_data.py | Malaysian FX, OPR and lending rates | None | High; default TLS adapter enforced by worker |
| fred_data.py | US macroeconomic time series and search | FRED_API_KEY | High |
| worldbank_data.py | Country indicators and comparisons | None | High |
| treasury_data.py | US debt and fiscal interest/exchange-rate records | None | Medium |
| sec_data.py | US filings and company facts | None | High; set SEC_USER_AGENT |

All five use requests. SEC also uses pandas and optionally beautifulsoup4 for HTML
parsing. requests and pandas are already declared by this project. No dependencies,
credentials, provider endpoints, or agent capabilities were changed while gathering.

## Provenance

Sources are byte-for-byte copies. manifest.json records relative upstream paths,
SHA-256 hashes, dependencies, API-key configuration and integration notes. The complete
upstream AGPL license is included in LICENSE; preserve it and source attribution.

## Usage examples

From the project root, with its virtual environment:

```powershell
.venv/Scripts/python.exe scripts/data_sources/bnm_data.py available
.venv/Scripts/python.exe scripts/data_sources/bnm_data.py currency USD
.venv/Scripts/python.exe scripts/data_sources/fred_data.py series CPIAUCSL 2024-01-01 2026-10-08
.venv/Scripts/python.exe scripts/data_sources/worldbank_data.py economic_snapshot MYS
.venv/Scripts/python.exe scripts/data_sources/treasury_data.py debt 2026-01-01 2026-10-08
```

Only the first example is metadata-only; the other examples make provider requests.
The run_script worker requires SEC_USER_AGENT and sets that contact identity. Do not expose arbitrary URLs or
all upstream commands as unrestricted agent tools.

## Active Deep Agent integration

CompositeBackend maps /scripts/ to a read-only FilesystemBackend. The agent reads
/scripts/AGENTS.md for discovery, then calls run_script with an allowlisted command.
Parent/research dispatch policies, tool receipts and telemetry include this runner.
The worker verifies the current provider manifest hashes, sets SEC_USER_AGENT and replaces the BNM
TLS adapter with requests' default adapter while preserving bounded, read-only runtime access.

Execution is limited to four concurrent processes, 60 seconds per invocation and
64 KiB per output stream. Broad commands and arbitrary filing URLs are excluded.
Errors stay explicit. Live provider coverage has not been verified; the integration
checks include metadata-only execution, real graph discovery, read-only operations
and denial/output-limit behavior. Prefer narrow date ranges and filing limits.

## Deliberately excluded

- yfinance_data.py: overlaps existing yf_data.py and includes terminal/daemon machinery.
- economic_calendar.py: requires browser automation and scraping rather than a small API wrapper.
- compute_technicals.py: depends on a larger technicals package; existing Rust indicators
  are a better starting point for bounded, verified calculations.
- Broker/execution scripts: outside the current research-only capability policy.
- Hundreds of specialized regional/alternative feeds: add only for a defined research need.
