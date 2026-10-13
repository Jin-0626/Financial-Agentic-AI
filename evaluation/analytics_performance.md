# Offline analytics performance

Deterministic European call: spot/strike 100, maturity 1 year, rate 5%,
volatility 20%, dividend yield zero. Seven repetitions after warmup; Windows,
Python application 3.13, analytics worker 3.12, Rust 1.99 optimized release build.
No live providers were called. Full measurements: analytics_benchmark.json.

| Tree steps | Python calculation | Python worker end-to-end | Rust calculation | Rust MCP end-to-end | Reduction |
|---|---:|---:|---:|---:|---:|
|100|0.47 ms|381.66 ms|0.032 ms|29.17 ms|92.4%|
|1,000|43.10 ms|421.51 ms|0.467 ms|30.01 ms|92.9%|
|2,000|176.72 ms|562.10 ms|1.747 ms|31.62 ms|94.4%|

JSON encode/decode median was 0.020–0.024 ms. First Python-worker call was
598 ms; first native connection plus call was 1,572 ms. Worker end-to-end includes
process creation/imports for every isolated job. Native warmed calls reuse the
existing MCP connection. The sample runner also identifies Python import startup
as a substantial cost. No provider-speed inference follows from these results.

The CRR operation meets both migration thresholds and improves the smallest
measured workload. It is now routed through the existing native service. Prices
matched the Python implementation within 1e-8 on these workloads. Targeted tests
cover expiry, known prices, cancellation, invalid probability and overflow;
Python remains a parity implementation and benchmark baseline.

These measurements do not justify migrating NumPy/SciPy-based operations or
claiming every input shape has been benchmarked. Broader numerical edge cases,
worker cancellation/concurrency tests and library-operation coverage remain
required before declaring the complete analytics integration verified.
