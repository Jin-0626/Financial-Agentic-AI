# Approved production evaluation

Run `python evaluation/run_live.py` against the local real server. The ten cases execute twice sequentially with isolated evaluation identities/conversations. Results and reports are stored in .review-tmp/evaluation/<campaign>/; no existing portfolio is modified. Live services and the configured model are used.

Metrics: completion, duration, first visible token, first tool result, cumulative reported tokens, reuse, tool requests/results/failures and required report sections. A first tool result is NOT automatically verified evidence. Dollar costs, cache-adjusted billing, per-provider latency and input/output token splits cannot be inferred from the existing SSE events and remain unavailable unless independently captured.

Quality review compares material report claims against actual provider receipts and checks fiscal scope, currency scale, EPS units, calculations and sources. Heading checks alone are not a factual-accuracy score. Unknown-ticker runs are deliberate recovery cases and are reported separately without removing failures from the all-case denominator. Stop after three consecutive HTTP/network infrastructure failures; agent failures remain part of the campaign.

Test fixtures and live results must not be combined. Twenty runs are observations, not an SLA or proof of a sub-3% production failure rate. Preserve raw event outcomes; no failures may be discarded. No credentials are stored in evaluation artifacts.
