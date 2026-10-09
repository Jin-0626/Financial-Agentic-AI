# Local data-source library

This folder contains 320 root provider/support Python scripts and 26 exchange-adapter files migrated from the local Fincept reference, plus the referenced root JSON catalogs. Files preserve their source layout and upstream license. manifest.json records hashes and imports.

Agents discover these files at /scripts/data_sources/. Read files in bounded windows. Copied files are NOT automatically registered with run_script. The five curated providers execute from this folder and retain their allowlisted contracts. See curated-providers.md.

Some adapters require optional packages, credentials or external services. Exchange adapters may contain trading operations: copying them does not enable live trading. Do not execute arbitrary source files or install every dependency together. Each provider requires a reviewed CLI, read-only command allowlist, limits and tests before registration.

No imports or runtime reads from the external sample checkout are needed for these copied files. Any dependencies on other not-yet-migrated packages remain pending; consult manifest.json. Root scripts include shared helpers and product utilities, not only data providers. Analytics and strategy modules are outside this source collection.

Local active adapters: yf_data.py, yh_market.py, fmp_client.py and alpha_vantage_client.py. Application imports use scripts.data_sources. The common app.providers adapter retains symbol, quote and history validation.
