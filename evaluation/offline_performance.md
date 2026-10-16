# Offline Rust migration results

Measured on 2026-10-14, Windows 11, Rust 1.99 release, application Python 3.13 and
isolated analytics Python 3.12. Seven warmed repetitions per workload; no live
providers or saved financial data. Raw results: [offline_benchmark.json](offline_benchmark.json).

The first five operations are Priority 1; the remainder are Priority 2. Fifteen
operations passed numerical parity and the end-to-end gates: at least 20% reduction
on typical inputs and no more than 10% regression on small inputs. Their production
catalog now dispatches to Rust with no Python fallback.

| Operation | Python worker typical ms | Rust MCP typical ms | Reduction | Production |
| --- | ---: | ---: | ---: | --- |
| moving_average | 381.64 | 33.78 | 91.1% | Rust |
| relative_strength_index | 397.16 | 36.24 | 90.9% | Rust |
| bollinger_bands | 442.05 | 42.24 | 90.4% | Rust |
| portfolio_performance | 408.54 | 33.02 | 91.9% | Rust |
| historical_risk | 399.09 | 31.06 | 92.2% | Rust |
| net_present_value | 383.56 | 29.79 | 92.2% | Rust |
| internal_rate_of_return | 993.21 | 29.50 | 97.0% | Rust |
| private_market_equivalent | 392.89 | 34.20 | 91.3% | Rust |
| black_scholes | 388.03 | 29.22 | 92.5% | Python; parity hold |
| implied_volatility | 1125.47 | 31.55 | 97.2% | Rust |
| forward_price | 373.41 | 29.21 | 92.2% | Rust |
| bond_price_duration | 393.91 | 30.02 | 92.4% | Rust |
| dividend_discount | 386.16 | 29.62 | 92.3% | Rust |
| weighted_average_cost_of_capital | 423.91 | 31.10 | 92.7% | Rust |
| compound_growth | 394.77 | 29.69 | 92.5% | Rust |
| statement_ratios | 418.97 | 31.19 | 92.6% | Rust |

## Precision and compatibility

Monetary outputs compare within absolute `1e-8`; floating metrics use absolute `1e-8`
and relative `1e-9`. Warmup/null alignment, flat series, complete loss, negative
rates, missing denominators, invalid requests, concurrency, cancellation and overflow
have regression coverage. Rust output schemas and the existing 1 MiB frames remain enforced.

Black–Scholes met the latency gate but fails the monetary gate in four of 24 seeded
large-magnitude cases, despite improved error-function accuracy. It remains Python
and its Rust prototype is not registered as a production MCP tool. See
[option stress results](option_stress_parity.json). Implied-volatility outputs match
the reference on those stress cases and on the timed workloads.

Each isolated worker is newly created per invocation. Warmed end-to-end measurements
include that startup, validation, JSON and transport; pure calculation timings are
recorded separately. Simple scalar formulas can be equally fast or faster in Python
in-process; much of their end-to-end gain comes from lower isolated worker overhead.
Cold native connection startup is separate from the gate measurements.

Series workloads contain 30, 1,000 and 10,000 observations. Maximum Bollinger inputs
use a 2,520-point window. Bond stress uses 100 years and monthly coupons. IRR's stress
case uses 100 cash flows: the existing SciPy lower bracket over/underflows on much
longer conventional schedules, so these measurements do not claim success across
all 10,000-flow inputs. Repeated scalar rows exercise parameter ranges, not larger
observation counts. No live-provider or universal speedup claim follows.

## Priority 3 investigation

Three statistics prototypes matched references on the measured workloads but remain
evaluation-only; no complete MCP/worker migration gate has been run for them.
Normalization measurements include DataFrame extraction/validation plus JSON
serialization, with provider network time excluded. Ten symbols with 2,520 points
produce a payload above 1 MiB; 50 symbols produce about 5.5 MiB. Native normalization
is deferred within the unchanged framing contract. The 100-symbol case exceeds the
supported 50-holding portfolio limit and is explicitly a stress test.

See [investigation data](rust_investigation.json). No Rust normalization speedup is
claimed. Mature optimization, forecasting and ML implementations remain Python.

## Reproduce

```powershell
cargo +1.99.0 build --release --locked --workspace --bins --examples
uv run --frozen python scripts/setup_analytics.py
uv run --frozen python scripts/benchmark_offline.py
uv run --frozen python scripts/investigate_rust.py
```

Independent reference implementations live under `evaluation/baselines`; production
packages cannot import them. Black–Scholes prototype stress results are retained
separately from the approved timing gates.
