# Analytics workspace

In-house analytics documentation and script adapters live here. Agents and subagents can discover this folder at `/scripts/analytics/` using `ls`, `glob`, `grep`, and bounded `read_file` calls.

Deterministic financial calculations live in `crates/quant-engine`; adapters should use the shared Rust engine rather than duplicate its formulas in Python. External data retrieval belongs in `/scripts/financial_sources/`.

This folder is read-only to agents. Files placed here are not automatically executable: execution adapters must be explicitly registered with documented inputs, output contracts, and limits. No runtime dependency on the external sample folder is permitted.

Currently available Rust dividend functions: `gordon_growth`, `preferred_stock`, and `staged_dividends`. These are library functions; agent-tool registration is pending.

See catalog.json for local dividend and fixed-income functions. Fixed-coupon bond valuation includes dirty price, Macaulay duration and modified duration. It supports coupon-date settlement with regular payments; accrued interest, callable bonds and irregular payment schedules are not implemented.
