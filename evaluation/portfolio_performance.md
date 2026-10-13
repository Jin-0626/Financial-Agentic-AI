# Portfolio performance investigation

The change batches import quote retrieval at four unique symbols per batch. It preserves
input-order error selection, quote currency checks, preview fingerprints, and atomic
commit behavior. Cancellation drains the current batch, including repeated cancellation;
no later batch starts. Concurrent calls can issue up to three additional provider requests
before discovering an error that previously stopped sequential retrieval immediately.
The bound is per import, not a global provider rate limit.

## Workloads and boundaries

Deterministic quote responses sleep 10 ms per call. No live Yahoo/model performance is
inferred. Portfolio summaries/imports use 10 and 50 symbols because the application and
Rust ledger cap holdings at 50. Normalization separately covers 10 and 100 symbols with
252 and 2,520 daily points. Imports contain 100 or 2,000 transactions.

Measurements separate Python validation/normalization, local JSON conversion, pure
release Rust calculation, and MCP calls including worker startup. Provider wall time is
recorded from the last warmed run. Pure Rust timings exclude deserialization. JSON
round trips exclude pipe transport; MCP durations include transport and processing.
Connection startup is reported separately. Run commands are in evaluation/README.md.

## Initial isolated import measurements

These timings exclude native ledger execution, use a mocked ledger, and are medians of
three warmed runs while the release build was active. Full release-backed measurements
are reported separately below.

| Transactions | Symbols | Sequential ms | Batched ms | Reduction |
| --- | --- | --- | --- | --- |
| 100 | 10 | 108.49 | 34.29 | 68.4% |
| 100 | 50 | 535.06 | 143.17 | 73.2% |
| 2,000 | 10 | 133.42 | 56.57 | 57.6% |
| 2,000 | 50 | 558.32 | 165.92 | 70.3% |

## Rust migration decision

Financial accounting, valuation, ratios, risk and performance are already native.
Price-history normalization remains a candidate, but a Rust normalization speedup has
not been demonstrated. Its interface would still require DataFrame extraction and
output serialization. The existing MCP frame limit is 1 MiB; large normalized histories
exceed that limit and cannot be treated as supported native requests. This change does
not alter those limits or introduce a new native interface. It implements the measured
provider-wait improvement instead of claiming an unmeasured language migration benefit.

The 2,520-point workload is a normalization stress test: a daily 5y portfolio request
normally has fewer trading-day rows. General JSON/worker transport optimization and
large-history framing remain follow-up work, not part of this change.

## Release-backed results (2026-10-10)

Windows 11, Python 3.13.15, Rust 1.99 release build. Compilation finished before
measurement. Raw observations and revision metadata are in
[portfolio_performance_results.json](portfolio_performance_results.json).

| Transactions | Symbols | Sequential preview ms | Batched preview ms | Reduction |
| --- | --- | --- | --- | --- |
| 100 | 10 | 152.95 | 77.36 | 49.4% |
| 100 | 50 | 577.71 | 189.29 | 67.2% |
| 2,000 | 10 | 357.98 | 283.78 | 20.7% |
| 2,000 | 50 | 791.84 | 395.63 | 50.0% |

The measured reduction exceeds 20% and 10 ms in every import workload. Summary timing
was approximately 47 ms for 10 positions and 72 ms for 50 after the change; summaries
were not modified. Connection initialization measured 1.02–1.44 seconds, and the
first empty-summary call measured 115–157 ms. These cold observations are not warmed
latency estimates and vary across processes.

Release ledger computation measured 0.07–1.53 ms, versus about 45–212 ms through MCP
and the per-call worker. Python import validation measured 1–22 ms and local argument
JSON conversion 0.15–3.08 ms. This exposes substantial native-boundary/worker overhead;
it does not isolate how much belongs to each transport/validation stage.

Unchanged normalization measured about 8–657 ms and normalized JSON conversion
1.35–153 ms. Ten symbols with 2,520 points produced 1,098,010 bytes; 100 symbols with
252 points produced 1,085,990 bytes, both already over 1 MiB before MCP framing.
The frame restriction and large-history limitations predate this change. A Rust
normalization prototype and a transfer strategy would be required to demonstrate a
safe improvement; this work makes no claim that such a migration met the threshold.

## Verification

- Final Python suite: 190 passed, 1 optional Docker worker-recovery test skipped.
  Includes PostgreSQL, native engine, telemetry and targeted import concurrency checks.
- Three PostgreSQL integration tests against an isolated PostgreSQL 17 container.
- Rust workspace: 27 tests, formatting check, strict Clippy including the new example,
  optimized engine build and optimized calculation benchmark build.
- Frontend TypeScript and Vite production build.
- Final diff whitespace check. No live-provider benchmark or deployment was performed.

The optional Docker worker-recovery test remains outside the ordinary run unless its
explicit integration flag is configured. No test failure was introduced or observed.
