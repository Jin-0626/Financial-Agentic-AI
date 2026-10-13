# Financial terminal workspace

The terminal is the landing page. Legacy Portfolio and Research URLs open their
existing workflows inside the terminal; Traces remains accessible separately.
Portfolio records and conversations stay in PostgreSQL. Layouts, public instrument
settings and watchlists are stored locally under the organization/user identity.
Private holdings are never stored as terminal layout state.

## Implemented panels

| Stage | Panels and behavior | Coverage limits |
|---|---|---|
| 1 | Drag/resize/add/remove, layouts, symbol palette, charts, quotes, watchlist, news, portfolio, docked Research | Yahoo exchange coverage and timestamps; public feeds may be delayed |
| 2 | Paginated MY/US screeners, sector treemap, listed options, crypto board and asset-ID charts | Provider-listed screener universe; heatmap covers largest 250 matches, not the full market; Bursa options unavailable |
| 3 | Treasury yield curve, VIX/index/ETF proxies, BNM OPR, economic and earnings calendars | FRED key required for official Treasury observations; many events lack reference periods; Bursa earnings may be missing |

The global palette uses Ctrl/Cmd+K. Enter selects; Shift+Enter also adds a watchlist
entry; `>` searches workspace commands. Alt+1–8 adds chart, quote, news, screener,
heatmap, crypto, options and portfolio. Alt+9 toggles Research. Expand a panel for
reading or portfolio editing. Closing Research retains its state and active request.
Public context is snapshotted at submission. Include selected portfolio is an
explicit action; it sends a bounded holdings snapshot within that user's research
request. Model context is labelled unverified and must be checked against tools.

## Market contracts

Authenticated `/api/market/*` routes expose capability discovery, search, quote,
OHLCV, news, screener, US directory, options, crypto, macro, economic calendar and
earnings history. Records carry source, fetch time, observation time and coverage.
Unavailable and stale statuses are explicit; missing numbers are not replaced by
zero. Display OHLCV is unadjusted, separate from adjusted portfolio accounting.

Chart ranges are 1D, 5D, 1M, 6M, YTD, 1Y, 5Y and MAX. Long equity ranges use weekly
or monthly bars; intraday availability is provider-dependent. Indicators use
aligned warmup gaps. VWAP resets by session for intraday ranges and is range-anchored
otherwise. Crypto volume is unavailable from the OHLC endpoint and VWAP cannot be
computed without volume. Price flashes respect reduced-motion settings.

Providers are fixed and allowlisted. Requests are bounded, redirects are rejected
by new HTTP clients, credentials remain server-side, and concurrency is capped at
four per provider. Shared single-flight caching has 256 entries, a 16 MiB budget and
2 MiB maximum responses. Clients share subscriptions, stop hidden-panel/tab polling,
prevent overlap and back off after errors. Quotes poll every 15 seconds; crypto and
options every 30 seconds; screening/heatmaps/intraday history every minute; news
every two minutes; other history every five minutes; calendars every 15 minutes.

`COINGECKO_DEMO_API_KEY`, `FRED_API_KEY` and existing `FMP_API_KEY` are optional
server configuration. Access and public endpoint availability are not guaranteed.
Calendar actuals are derived only for matched official series and explicit reference
periods, using the event-date FRED vintage. Most public calendar entries lack that
metadata and correctly show unverified outcomes. Earnings returns are adjusted
close-to-close changes for the first trading date after a known announcement date;
unknown announcement times retain null returns. They do not establish causation.

## Dependencies and attribution

This implementation uses independently written adapters and UI components inspired
by the local OpenTerminal sample. No Next.js, Express or SQLite service was added.
React Grid Layout provides panel geometry. Lightweight Charts was created by
TradingView, Inc.; its required chart attribution link is enabled. License and
notice: https://github.com/tradingview/lightweight-charts/blob/master/NOTICE
Existing portfolio accounting remains native; no speculative Rust migration was made.

## Verification

Deterministic tests cover cache single-flight/concurrency/cancellation, throttling,
missing values, validation/auth, instrument identity, news deduplication, calendar
period matching, earnings session selection, indicator fixtures, layout persistence,
drag/resize, settings, pinned symbols, identity isolation, and research sidebar closure.
Live provider probes and the final check totals are recorded in terminal-verification.md.
