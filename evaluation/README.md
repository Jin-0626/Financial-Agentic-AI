# Approved production evaluation

Run `python evaluation/run_live.py` against the local real server. The ten cases execute twice sequentially with isolated evaluation identities/conversations. Results and reports are stored in .review-tmp/evaluation/<campaign>/; no existing portfolio is modified. Live services and the configured model are used.

Metrics: completion, duration, first visible token, first tool result, cumulative reported tokens, reuse, tool requests/results/failures and required report sections. A first tool result is NOT automatically verified evidence. Dollar costs, cache-adjusted billing, per-provider latency and input/output token splits cannot be inferred from the existing SSE events and remain unavailable unless independently captured.

Quality review compares material report claims against actual provider receipts and checks fiscal scope, currency scale, EPS units, calculations and sources. Heading checks alone are not a factual-accuracy score. Unknown-ticker runs are deliberate recovery cases and are reported separately without removing failures from the all-case denominator. Stop after three consecutive HTTP/network infrastructure failures; agent failures remain part of the campaign.

Test fixtures and live results must not be combined. Twenty runs are observations, not an SLA or proof of a sub-3% production failure rate. Preserve raw event outcomes; no failures may be discarded. No credentials are stored in evaluation artifacts.

## Offline portfolio performance

Build the release engine and calculation benchmark from the repository root:

```powershell
cargo +1.99.0 build --release --locked -j 2
cargo +1.99.0 build --release --locked -p quant-engine --example portfolio_benchmark
uv run --frozen python -B evaluation/benchmark_portfolio.py --baseline
uv run --frozen python -B evaluation/benchmark_portfolio.py
```

`--baseline` loads the committed import-preview implementation from HEAD in memory.
Use it before committing the optimization; after committing, HEAD is no longer the
original baseline. Neither run changes portfolio records or calls live providers.
Quotes have a deterministic 10 ms delay. The harness reports median warmed timings,
connection startup, Python validation/normalization, JSON round trips, in-process
release Rust computation, and native MCP calls including per-call worker startup.
These separately measured components do not sum exactly to request latency.

Summaries and imports use 10 and 50 symbols, respecting the existing 50-holding
limit. Normalization uses 10 and 100 symbols with 252 and 2,520 points each; imports
use 100 and 2,000 transactions. The 100-symbol normalization case is a stress case,
not a supported portfolio size. DataFrame value extraction is a partial measurement
and excludes date extraction and validation. JSON timings measure local conversion,
not isolated pipe/network transport. Do not present these timings as live-provider
performance or as a measured Rust normalization speedup.

The native server currently caps a single MCP frame at 1 MiB. The harness records
normalized payload sizes; large history cases can exceed this existing boundary.
A future Rust normalization migration must include raw extraction, serialization,
MCP/worker overhead, and output transfer, and respect the frame limit. No Rust
normalization implementation or measured speedup is claimed by this benchmark.
