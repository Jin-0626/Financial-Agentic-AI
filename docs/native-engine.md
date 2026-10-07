# Native engine — Phase 2

All work is local. The Rust workspace contains pure `quant-engine` calculations,
`data-pipeline` immutable snapshots/provider normalization, and the in-house
`financial-engine` MCP executable. The existing API, report schema, SSE behavior,
and Python providers remain active. Agent cutover and authenticated forecast
registration are Phase 3; administrative imports are not public endpoints.

## Build and test

Rust 1.99 or newer, with a native linker, is required. On Windows install the
MSVC C++ build tools before native builds. The current local host has Rust but
no MSVC linker; Linux builds use the local `rust:1.99-slim-bookworm` Docker image.

```powershell
cargo build --locked -j 2
cargo test --locked -j 2
cargo fmt --all --check
cargo clippy --locked --workspace --all-targets -- -D warnings
```

`Cargo.lock` retains resolved dependencies. Application crates forbid unsafe code
and deny Clippy `unwrap_used` and `expect_used`; dependencies can still panic or
fail allocations. A successful build is not a production-readiness assertion.

## Organization scope and ingestion

Set `ENGINE_ORG_ID` from trusted application configuration and `ENGINE_DATA_ROOT`
to an application-managed directory. The engine does not accept organization IDs
as tool arguments. Separate supervisor instances are required per organization.
This is an isolation boundary, not bearer-token authentication; never expose the
administrative commands or stdio stream directly to untrusted clients.

```powershell
$env:ENGINE_ORG_ID = 'local-org'
$env:ENGINE_DATA_ROOT = '.engine-data'
$env:OTEL_ENABLED = 'false'
python -m scripts.native_engine yahoo-prices --ticker 0157.KL --lookback-days 252
python -m scripts.native_engine yahoo-statements --ticker 0157.KL
python -m scripts.native_engine import --file forecast.json
```

`ENGINE_COMMAND` can specify a JSON argv list for a native executable or local
Docker invocation. It is administrator configuration, never LLM-provided code.
`financial-engine schema` emits the snapshot and four tool JSON Schemas.
`financial-engine import` accepts one normalized JSON snapshot on stdin and emits
an opaque SHA-256 ID. A forecast ID is the imported forecast snapshot ID.
`financial-engine fetch-fmp` reads `ENGINE_TICKER` and `FMP_API_KEY` from environment,
fetches bounded financial statements from the fixed FMP host and imports them.
No credentials or response bodies enter telemetry. News, macro, and other existing
provider coverage remain in Python; no unvalidated provider parity is claimed.

Snapshots carry schema version, ticker, currency, unit multiplier `"1"`, provider,
source reference, retrieval time, as-of date, organization, and typed dataset.
Price rows contain strictly increasing dates, adjusted close, and optional volume.
Statement records contain exact period end, annual flag, and decimal-string account
values. Missing accounts are omitted. Conflicting dates/accounts and mixed currency
are rejected. Yahoo ingestion uses `auto_adjust=True` and preserves price precision.
Monetary inputs/results are decimal strings; rates in MCP arguments are fractions.

A forecast dataset has `kind: "forecast"` and a `forecast` object containing:

```json
{"valuation_date":"2024-12-31","cash_flows":[{"date":"2025-12-31","amount":"100"}],"net_debt":"50","diluted_shares":"10"}
```

Supply it inside the full snapshot envelope, with matching as-of date and currency.
Cash flows are annual run-rate free cash flows; dates strictly follow valuation.
DCF discounts ACT/365 fractional years and uses the final annual cash flow in the
Gordon terminal value. It preserves negative valuations and requires WACC greater
than terminal growth. It never generates a forecast on the user's behalf.

Snapshot JSON and price Parquet bodies use temporary files, fsync, and atomic
promotion, with hashes verified before reuse. Latest pointers resolve at admission
into immutable IDs; calculations do not follow changing pointers mid-execution.
Application storage must be writable only by its service account. Hashes detect
corruption, not malicious rewriting by an administrator with host access.

## MCP contract and execution

Run `financial-engine serve`. Stdout contains only newline-delimited UTF-8 JSON-RPC;
stderr is diagnostic output. Initialize negotiates protocol version `2025-11-25`,
then the client sends `notifications/initialized`. Supported methods are ping,
tools/list, tools/call, and cancellation notifications. Close stdin for shutdown.
Notifications receive no response. Duplicate keys, unknown arguments, malformed
frames, unknown methods/tools, and oversized messages have explicit failures.

Four tools: `compute_discounted_cash_flow`, `calculate_historical_var`,
`extract_financial_ratios`, `run_monte_carlo_simulation`. Schemas describe inputs and
success/error output envelopes. Results include `structuredContent` and matching
JSON text; domain failures use `isError`, protocol failures use JSON-RPC errors.
Optional snapshot IDs pin reviewed datasets. Exact ratio periods are mandatory.

Historical VaR sorts one-day fractional losses (`-simple_return`) and uses nearest
rank; ES includes all losses at or above VaR. Normal VaR uses sample standard
deviation. Annual volatility uses 252 trading days. Ratios preserve missing or
nonpositive denominators as unavailable with reasons. Monte Carlo is seeded ChaCha8
GBM calibrated to log-return mean and sample volatility; it returns terminal summary
statistics and an opaque, hash-verified organization-owned summary artifact ID, not every path or a claimed price prediction. Sparse data and illiquidity
remain explicit limitations. Pure portfolio-return, SMA, and Wilder RSI functions
are available for parity fixtures, but are not additional MCP tools.

Defaults: one MiB frames, four worker processes, 64 waiting calls, 30-second deadline
including admission wait. `ENGINE_COMPUTE_TIMEOUT_MS` may reduce, never increase,
that deadline. Cancellation sends a cooperative token message with at most 100 ms of control/grace wait,
then kills and reaps the worker if needed. Deadline expiry hard-kills immediately.
A killed/crashed worker yields a sanitized error; a later call starts a fresh worker.
There is no restart loop for invalid financial inputs. EOF cancels pending calls.
Output writes and shutdown are bounded; a stalled/broken output pipe cancels work.

Provider retries are limited to two after the initial request. Redirects are disabled,
responses bounded, Retry-After respected up to five seconds, with jitter. The FMP
circuit opens after five failed operations for 30 seconds and permits one recovery
probe. Cancelled probes release their lease. Provider failure isolation applies per
client/provider. XML extraction rejects DTD/entities, bounds size/depth/facts, and
accepts numeric facts only in standard US-GAAP/IFRS namespaces. It does not infer
contexts, units, consolidated scope, or company extension taxonomies; callers must
explicitly normalize/verify extracted facts before importing financial statements.

## Tracing and validation

`params._meta.traceparent` and optional `tracestate` carry W3C context. The supervisor
creates `mcp.tools.handle`; worker `quant.compute` spans inherit it across processes.
Queue depth, write wait, tool duration, errors, and recovery starts are real instruments.
OTLP/gRPC uses `financial-engine` with ten-second metric export. Target filtering
excludes exporter internals; spans never include argument bodies or organization IDs.
Missing context starts a trace; invalid context increments a diagnostic counter.
Use the Phase 1 Collector configuration in `docs/observability.md`.

`tests/test_native_engine.py` runs opt-in subprocess interoperability checks using
`ENGINE_TEST_COMMAND`, a JSON argv template ending in the executable. Templates may
use `{data_root}`, `{timeout_ms}`, `{container_name}` placeholders. Tests append
serve/import/schema. The usual Python regression suite remains applicable.
Production cutover requires authorization, durable DAG/recovery, financial/provider
parity coverage, Windows verification, load checks, and dependency audit gates.

## Local validation record

Validated on Windows host AMD Ryzen 7 6800H (8 physical/16 logical cores),
Docker Desktop Linux engine with 16 CPUs and approximately 7.4 GiB available RAM.
Protocol benchmarks ran in containers constrained with `--cpus 4`, using the
unoptimized locked Rust build and Python 3.13 on the Windows host. The 100-request
ping sample measured p95 1.03 ms, including stdio/Docker boundary overhead. This
passes the local 20 ms synthetic no-op threshold, not a production research SLA.
The load check sends 96 calls, observes explicit overload and cancellation, and
verifies that the server remains responsive. Real model/provider load is later work.

Native tests cover independent DCF/risk values, negative cash flows, invalid rates,
missing/zero denominators, adjusted returns, duplicate dates, currency/units,
Monte Carlo reproducibility, portfolio-return alignment, malformed JSON, frame caps,
strict schemas, isolated artifacts, circuit recovery, deadlines, concurrent replies,
and a real worker kill followed by successful calculation. The Python ratio parity
check executes the existing financial processor against known expected values.
Live native smoke verification received six Python/supervisor/worker spans with
shared trace ID and correct parentage, plus queue/write/duration/active metrics.
All snapshots in these checks are synthetic. Live provider credentials are not needed.
Windows-host-to-Linux stdio is verified; a Windows-native binary still needs the
MSVC linker and its own interoperability run before that acceptance gate closes.

Final local checks: 143 Python tests passed with both native executables enabled
and no skips; 19 Rust tests passed; locked Cargo build, strict Clippy, rustfmt,
frozen offline Python sync, package compatibility, and diff whitespace checks passed.
The native smoke check verified all six spans, shared trace continuity, metrics,
and privacy after cooperative cancellation was connected to worker execution.
No GitHub push, deployment, provider deletion, or API cutover was performed.
Dependency vulnerability auditing and Windows-native verification remain acceptance
gates before production cutover; live vendor coverage is preserved in Python.
