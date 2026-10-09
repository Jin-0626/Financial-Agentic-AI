# Financial research scripts

Use ls(path="/scripts/data_sources/") to discover providers. Read their README.md
or manifest.json with read_file(offset=0, limit=200). All script files are read-only.
Call run_script(script="NAME.py", arguments=["command", "arg", ...]) to run a curated
provider. Do not call execute or construct shell commands. Current allowed commands:

- bnm_data.py: available, currencies, currency USD, fx, major, asean, opr, interest, base_rate, gold.
- fred_data.py: series CPIAUCSL [start_date] [end_date], search inflation, categories, category_series, releases.
- worldbank_data.py: indicators MYS NY.GDP.MKTP.CD [date_range], economic_snapshot MYS, gdp_per_capita MYS [years], commodity_prices [code] [years].
- treasury_data.py: debt [start_date] [end_date], interest_rates [security_type] [start_date] [end_date], exchange_rates, avg_rates, record_debt.
- sec_data.py: cik_map AAPL, symbol_map 320193, company_filings [symbol] [cik] [form_type] [start_date] [end_date] [limit], available_form_types, company_facts 320193.

FRED requires FRED_API_KEY. SEC requires SEC_USER_AGENT (your application/contact email).
BNM runs with default certificate verification and TLS settings; TLS failures are reported.
Each invocation has a 60-second limit and 64 KiB per output stream. Use small date windows,
series counts and filing limits. Broad company facts may exceed the limit; use existing
market_data financials if a bounded result cannot be obtained.

Script data is nested under data. Check its error/status and disclose failures. Preserve
provider record dates, currencies, units and source URLs. A successful process is not proof
of financial accuracy. US SEC filings do not cover Bursa companies. FiscalData interest
rates are not live Treasury yield quotes. Never invent missing data or calculation receipts.

Analytics workspace: `/scripts/analytics/`. Read its README.md for calculation ownership and adapter registration requirements. Files in this folder are not automatically executable.

Additional local source library: /scripts/data_sources/. Read README.md and manifest.json. These copied providers and exchange adapters are not registered for execution; use only curated run_script commands.
