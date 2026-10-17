# Native market ranking

Desktop MY and US screener subscriptions use authenticated transport endpoints:

- `/api/market/provider/us-page?offset=0`: one fixed 250-row Yahoo page and its total.
- `/api/market/provider/my-symbols`: the configured exact Malaysian ticker set.
- `/api/market/provider/equity-info?instrument=yahoo:5326.KL`: one exact-symbol quote.

Rust owns pagination, four-worker snapshot assembly, completeness and duplicate
validation, decimal quote normalization, identity/currency validation and ranking.
Dropping snapshot work aborts pending Tokio requests. The backend must be updated
together with the desktop. Existing `/screener` and normalized snapshot routes remain
available for existing HTTP consumers, but are not used by this native pipeline.

Rust filters, ranks and paginates the complete snapshot. Decimal prices remain
exact; missing sort values stay last in either direction and ties use canonical
instrument IDs. Snapshot counts are checked before publication, with a 20,000-row
ceiling and a 2 MiB limit per transport response. The complete US universe is no
longer transferred as one large JSON response. Unexpected zero broad-US coverage
is unavailable and preserves the engine's last successful result as stale.
Snapshots are shared across native table sorts and filters for 60 seconds,
scoped to the provider instance (backend and credentials). Backend retry delays
are shared too; the engine retains its existing stale-data and generation rules.

Python/yfinance retains Yahoo session handling and individual HTTP requests, with
the existing backend-wide throttle/cache. It projects finite, bounded transport
fields; it does not assemble or rank desktop equity snapshots. Non-equity resources,
research and portfolio persistence still retain Python implementations. Malaysia
retains its configured tracked set; US retains the broad provider universe, including
OTC. No new data sources or dependencies are introduced.
