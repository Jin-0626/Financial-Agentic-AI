# Financial research scripts

This is the Financial Deep Agents read-only provider workspace. Discover files with
ls(path="/scripts/data_sources/") and read curated-providers.md or manifest.json in
bounded windows using read_file(offset=0, limit=200). Only the curated commands below
are executable through run_script(script="NAME.py", arguments=["command", ...]).
Do not construct shell commands or modify provider files.

- bnm_data.py: available, currencies, currency USD, fx, major, asean, opr, interest, base_rate, gold.
- fred_data.py: series CPIAUCSL [start_date] [end_date], search inflation, categories, category_series, releases.
- worldbank_data.py: indicators MYS NY.GDP.MKTP.CD [date_range], economic_snapshot MYS, gdp_per_capita MYS [years], commodity_prices [code] [years].
- treasury_data.py: debt [start_date] [end_date], interest_rates [security_type] [start_date] [end_date], exchange_rates, avg_rates, record_debt.
- sec_data.py: cik_map AAPL, symbol_map 320193, company_filings [symbol] [cik] [form_type] [start_date] [end_date] [limit], available_form_types, company_facts 320193.

FRED requires FRED_API_KEY. SEC requires SEC_USER_AGENT with your application name
and contact email. Preserve certificate verification; report TLS and provider errors.
The worker verifies each provider against the current manifest before execution.

Each call has a 60-second deadline and a 64 KiB limit per output stream. Request
small date windows and bounded results. Inspect nested data for error/status fields;
a successful process alone does not verify financial accuracy. Preserve currencies,
units, fiscal periods, provider record dates, and returned source URLs.

SEC covers US filings rather than Bursa companies. Treasury FiscalData rates are
not live Treasury yield quotes. Disclose missing evidence instead of inventing it.

The /scripts/analytics/ library contains supporting financial analysis code. Files
there and unregistered providers are not automatically executable by agents.
