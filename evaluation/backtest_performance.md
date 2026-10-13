# Rust backtesting performance

Synthetic, offline closing-price workloads measured on Windows-11-10.0.26200-SP0 with application Python 3.13.15 and Rust 1.99 optimized release builds. No providers or model calls were made. Five warmed end-to-end repetitions per workload; pure Rust medians use seven repetitions. Raw measurements: [backtest_benchmark.json](backtest_benchmark.json).

| Strategy | Bars | Python calculation | Rust calculation | Python worker total | Rust MCP total | Reduction |
|---|---:|---:|---:|---:|---:|---:|
| buy_and_hold | 30 | 0.16 ms | 0.023 ms | 391.00 ms | 30.63 ms | 92.2% |
| buy_and_hold | 1000 | 3.45 ms | 0.730 ms | 384.17 ms | 64.28 ms | 83.3% |
| buy_and_hold | 10000 | 32.26 ms | 7.257 ms | 427.25 ms | 353.64 ms | 17.2% |
| moving_average_crossover | 30 | 0.15 ms | 0.021 ms | 363.30 ms | 29.44 ms | 91.9% |
| moving_average_crossover | 1000 | 3.66 ms | 0.729 ms | 359.69 ms | 61.78 ms | 82.8% |
| moving_average_crossover | 10000 | 36.73 ms | 7.655 ms | 445.65 ms | 364.92 ms | 18.1% |
| rsi | 30 | 0.13 ms | 0.018 ms | 370.46 ms | 28.65 ms | 92.3% |
| rsi | 1000 | 3.91 ms | 0.757 ms | 364.47 ms | 62.75 ms | 82.8% |
| rsi | 10000 | 39.53 ms | 7.290 ms | 435.87 ms | 364.45 ms | 16.4% |

End-to-end results include process creation, transport and validation. Rust reuses the MCP connection but still isolates calculations in per-job workers. The Python baseline starts its analytics interpreter for each job. Pure calculation measurements exclude JSON parsing/serialization and process startup. Native cold connection plus the first 30-bar call measured 1644 ms; cold latency is not represented by the warmed reduction percentages.

At 10,000 bars, transport and validation dominate; the observed improvement is only 16–18%. These results do not demonstrate a uniform speedup across every workload or concurrent load. The synthetic crossover series compounds to unusually large values and is a numerical stress case, not a trading forecast.

Native integration compares all three strategies at 30, 1,000 and 10,000 observations against the retained Python reference. Trade dates/sides match; monetary outputs match within 0.00000001 on these workloads. Additional cases cover scientific-notation capital, high fees, flat/monotonic RSI, warm-up and prefix invariance. Decimal overflow/underflow returns errors. The native worker and RPC reply retain 1 MiB limits: workloads with too many trade records can return a bounded size error even when their input contains at most 10,000 bars.

The Backtesting API and agent analytics operation use the same Rust implementation. The Python implementation remains a parity/benchmark reference. Build and reproduction commands are in the README Backtesting workspace section.
