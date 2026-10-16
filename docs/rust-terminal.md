# Tauri financial terminal

The active frontend is a new Tauri + React market workspace. The Ratatui CLI and
its dependencies are removed. Tauri hosts the Rust engine directly; widgets use
typed IPC actions and snapshot events, never HTTP or SQLite APIs.

## Start the desktop

The optional broker-gateway integration has been removed. Market requests use the
existing backend's Yahoo Finance and CoinGecko routes; the seeded mock provider
remains available for offline development. Local databases and saved layouts are
not deleted by this removal.

```powershell
npm --prefix frontend ci
npm --prefix frontend run desktop:dev
```

Live HTTP mode is the default, with `5326.KL` and `6M` history. The desktop uses
the existing market adapters through `http://127.0.0.1:8000`. It never falls back
to synthetic prices when a provider is unavailable.
To package the Windows desktop, run `npm --prefix frontend run desktop:build`.
Visual Studio C++ Build Tools and WebView2 are required on Windows.

Start the lightweight live market gateway in a separate terminal. It reuses the
same authenticated routes and allowlisted provider adapters as the full backend,
without PostgreSQL or research initialization; portfolios use backend SQLite:

```powershell
uv run --frozen python -m uvicorn app.bootstrap.market_application:app --host 127.0.0.1 --port 8000 --http h11
```

Then launch the desktop. An already running full FastAPI backend works too:

```powershell
$env:TERMINAL_PROVIDER = 'http'
$env:TERMINAL_BACKEND_URL = 'http://127.0.0.1:8000'
npm --prefix frontend run desktop:dev
```

`TERMINAL_PROVIDER=http` and the loopback URL are defaults; these overrides are
optional. If startup fails, start/fix the gateway and use Retry connection.
For explicitly synthetic offline development, set `TERMINAL_PROVIDER=mock` before
launching. Mock mode uses a separate watchlist/settings scope and no backend.

When OIDC is configured, supply an externally obtained bearer token through
`TERMINAL_ACCESS_TOKEN`. Authentication configuration and verified identity come
from `/api/auth/config` and `/api/auth/me`; interactive login/token refresh are
deferred. Tokens remain in native memory and are not returned to the webview or
stored in SQLite. Remote backend origins require HTTPS; loopback HTTP is allowed.
`TERMINAL_PROFILE` selects a local profile (default `default`). Restart to change
provider, profile, backend, or token. No silent synthetic fallback occurs.

Plain browser/Vite preview displays a desktop-runtime-required message. The app
does not pretend to run a Rust engine inside the browser.

## Workspace

The dark shell uses the installed moomoo 16.34.27008 as its visual reference:
blue-gray surfaces, orange active controls, a 132px horizontal-label navigation rail
(48px collapsed), system UI text
and tabular numeric text. Original icons and components are used; no packaged
moomoo artwork, fonts, or binaries are shipped. New profiles start with a left
watchlist and central chart. The 36px right tool strip opens Quote, News or Calendar;
it starts closed. Below 1200px the inspector becomes an explicit drawer; closing it
releases its resource subscriptions. A 32px integrated title/search bar provides
native window controls and navigation history. More retains access to Crypto,
News, Screener, Macro, Backtesting and Traces; Research and Settings remain below.
Drag the inspector divider to resize it between 260 and 420 pixels. With the
divider focused, Left/Right changes width by 10 pixels and Home/End selects the
minimum/maximum; double-click resets to 290 pixels. The width is saved in the
identity-scoped view settings. The narrow-window drawer contains keyboard focus;
Escape closes it and returns focus to Instrument details. Chart axes use actual
panel dimensions so their text stays readable when docking or resizing.
The Compact toggle changes display density, and + Symbol focuses the add field.
View, density and named layouts commit to identity-scoped SQLite. Preferences
versions 1 through 6 migrate transactionally to version 7, preserving custom layouts,
selection, range, history and bindings. Future preference versions are rejected.
SQLite remains schema version 2. New profiles default to compact density; stored
density and custom layouts are preserved. View settings persist rail collapse,
inspector state/width, ticker mode, and market/portfolio/calendar subviews.

Chart panels offer candles or a closing-price line and an Indicators dialog for
SMA (5/10/20), EMA (20), Bollinger Bands (20, population standard deviation ×2),
Wilder RSI (14), MACD (12/26/9), and optional volume. Indicators start disabled
except volume. Edit visibility, periods (2–250), color and line width; MACD fast
must remain below slow. Calculations use the complete loaded unadjusted history,
with unavailable warm-up values, and do not request additional history. A flat
RSI window is 50. RSI/MACD panes share the chart's time axis and crosshair.

Draw offers trend lines, horizontal levels, rectangles and Fibonacci retracements
(0/23.6/38.2/50/61.8/78.6/100%). Drag in the price pane to create a drawing; select
it and drag its handles to edit. Escape cancels a gesture. With chart focus,
Delete removes the selected drawing; a Delete selected drawing button is also
available. Clear drawings asks for confirmation. Undo/redo retain up to 50
operations while that chart editor is mounted. Each panel/instrument can store
100 drawings, anchored in UTC timestamps and prices; range changes retain them.
Completed edits commit through the native engine, with Retry/Discard on failed
saves. Chart presets copy indicator settings but start with fresh IDs and no
drawings; closing a panel removes its drawing records only after the layout saves.

Each watchlist panel has a local symbol/name filter and row actions, opened with
the ellipsis button or right-click. Open Instrument changes selection; Add to List
and Remove from This List explicitly change membership. Manual Move Up/Down is
disabled while sorted. Filtering and sorting never rewrite stored member order.
Up/Down moves the cursor; Enter selects; clicking a data row selects immediately.

Drag a panel tab onto a highlighted edge to split, or the center to group tabs.
Resize dividers with the pointer or arrow keys; the Move menu is a keyboard
alternative to dragging. Close/maximize controls preserve the saved tree.
The contextual Workspace tools menu supplies Moomoo Market Desk, Market Overview, Portfolio Research, Screening Desk,
Two Chart Desk and Four Chart Desk
templates, panel restoration and named layout management. Ctrl+K opens the
screen/symbol/layout palette. Settings exposes connection identity, density,
saved layouts and validated keymap editing. Changing backend or tokens requires restart.

Panel instances have stable IDs and typed definitions. Add multiple chart/news
panels; use a panel's Symbol menu to pin an exact instrument or follow the active
selection. A chart's Symbol menu also controls Independent range; linked charts
can retain their own range while following the global instrument. The two-chart
preset uses 1M/6M; the four-chart preset uses 1D/1M/6M/1Y, with fresh stable panel
IDs and without replacing other saved layouts. Pinned chart ranges persist independently. Drag tabs within a group
to reorder them, or use the keyboard move controls. Existing saved arrangements
are retained rather than replaced by the new preset.

Crypto, News, Screener/heatmap, Options, Macro, economic/earnings Calendar and
global instrument search use fixed typed native market requests. The Rust engine
deduplicates requests, bounds concurrency and response sizes, honors retry delays
and preserves explicitly stale results after failures. Native subscriptions own
refresh schedules, with bounded actions, four concurrent resource loads,
generation checks and revisioned notifications. The webview releases hidden
owners and recovers resource state after focus changes; docking retains stable
owner IDs. Legacy bootstrap quote/history refresh stops when native panel ownership
begins. Native quote owners cover visible watchlist members, the active ticker,
the selected instrument and pinned charts, sharing identical requests. Candle buffers are
shared immutably between the compatibility snapshot and resource subscribers.
The existing backend still owns upstream normalization and throttling.

### Named watchlists and market browsing

SQLite schema version 2 migrates the previous membership into **My Watchlist**
in every existing namespace, retaining symbol order. Each watchlist panel can
bind a different list. Focusing a panel activates its list for the ticker,
`:add` and `:del`; the standalone Watchlist screen follows that active list.
Switching lists or removing members does not change the selected chart instrument.
Named lists, table columns and sort settings are local and identity-scoped;
they are not synced to moomoo, browser storage or the separate portfolio database.

Manage creates, duplicates, renames, deletes and orders lists. At least one list
is retained; limits are 20 lists, 128 members per list and 128 distinct instruments
per namespace. Bulk add accepts commas, semicolons and new lines and previews
normalized IDs, duplicates, invalid values and capacity errors before committing
atomically. Failed writes retain the draft. Bare equity symbols gain `yahoo:`;
CoinGecko identities must be explicit. No exchange suffix is guessed.

Columns controls visibility and order; Instrument is always shown. Header clicks
sort the displayed rows, with missing values last and exact decimal-string quote
comparisons. Saved order enables drag handles and Move up/down controls. Up/Down
moves the focused panel's cursor and Enter selects its instrument. Sorting does
not alter membership order.

Markets now offers an Overview plus MY Equities, US Equities and Crypto using the existing native
screener/crypto resources, provider pagination and supported sector filters.
Coverage is provider-dependent, not an exhaustive exchange listing. Open Instrument
changes the global selection without adding membership; explicit Add targets the
active list. The dashboard remains the chart workspace. Live checks are read-only;
these local list edits never write provider or server records.
Market Cap, Gainers, Losers and Volume Leaders use provider-side equity sorting.
Sector, minimum market cap, minimum percentage change and minimum volume filters
also run on the existing backend screener. Changing filters or sorting resets the
page. Symbol/name filtering only affects loaded results; Crypto sorting is also
local to returned results. MY, US and Crypto remember independent columns,
sorting and filters. Instrument is mandatory; other columns can be hidden and
reordered with accessible controls. Pagination starts at page one after restart.
Overview and the bottom ticker reuse native quote/history resources for Bursa
KLCI, S&P 500 and VIX. Ticker mode can switch to the active watchlist. Provider
coverage, observation times and limitations are available through data disclosures;
stale/unavailable markers and the mock-data warning remain visible.

Options aligns read-only calls and puts around normalized decimal strikes,
preserving duplicate contracts and unavailable fields, beside an underlying chart.
Previous/next expiry controls use provider-returned dates only. Explicit expiry
choices are session-local per underlying; Provider default expiry recovers from
an expired/unavailable choice. Calls/Both/Puts, provider ITM/OTM, minimum volume,
minimum open interest, contract search and nearest-21-strike filters operate on
the loaded chain. Nearest-strike mode requires an underlying quote. IV is displayed
as a percentage; contract details retain raw decimal strings and their own
observation time, with unavailable fields stated explicitly.
Select a contract quote with the pointer or Tab/Enter to inspect it. The wide
information pane replaces the underlying chart while inspecting a contract and
releases that hidden history subscription. Below 1200 pixels, contract inspection
uses a focus-contained drawer; Escape closes it and restores focus. Selecting a
contract never changes the globally selected underlying.
Markets and Options each support up to 20 named local presets (1–64 characters):
save, rename, duplicate, apply, delete and reset. Presets contain display settings,
not selected contracts, expiry dates or server records. Failed display writes keep
an unsaved draft with Retry/Discard. Preferences version 7 preserves existing
charts, drawings, named lists and layouts; SQLite remains schema version 2.
Calendar offers a current-week economic grid, date/country/impact filters and a
table view. Selected-instrument earnings are separate; arbitrary-date schedules
are not implied. News uses headline/detail panes without inventing article text.
Portfolio uses Holdings, Transactions and Performance tabs while retaining the
existing guarded editor/import workflows. Research activity is collapsible.

The screen contains quote metrics, an SVG OHLC chart, watchlist, command input,
provenance, freshness, latency, and uptime. There are no charting/widget libraries.
History ranges are 1D, 5D, 1M, 6M, YTD, 1Y, 5Y, MAX. Volume remains unavailable
when the provider omits it. Chart prices use unadjusted display history, distinct
from portfolio accounting. Hover for timestamp/OHLC/volume, drag to pan, and
scroll to zoom around the pointer. Charts start with the latest 120 candles and
allow 20–800 visible candles. Panning disables follow-latest; Reset view and
Follow latest restore it. Left/Right move the crosshair; Home/End navigate history
and +/- zoom. Symbol and range changes reset the viewport. Missing volume stays
unavailable. Watchlist sorting affects presentation only; membership order and
the keyboard cursor's instrument are preserved.

Up/Down moves the watchlist cursor; Enter selects. Click an instrument to select,
or `t` to cycle ranges. `:` opens the command prompt overlay; the bottom `:` button
also opens it. Enter submits and closes it; Escape dismisses it. In the prompt, Up/Down
browses persisted history, Escape clears/cancels, and Enter submits. Native input
editing supports Unicode. `:add <SYM>` / `:del <SYM>` persist watchlist edits
immediately. `:w` or Save workspace persists selection, range, and bounded history.
`:q` shuts down the engine and exits the desktop. Window close also shuts down
workers. Quitting does not implicitly save pending UI preferences.

Bare symbols gain `yahoo:`. Fully qualified `coingecko:bitcoin` identities are
preserved; exchange suffixes and crypto IDs are never guessed. Startup and IPC
errors are visible. Engine snapshots carry monotonically increasing revisions;
older updates cannot replace newer state. Burst updates are coalesced before IPC;
quote-only updates omit unchanged candle buffers. Full startup/recovery snapshots
remain available.

## Architecture and storage

The root workspace retains `terminal-core`, `terminal-providers`,
`terminal-storage`, `terminal-engine`, and `terminal-ui`, plus the original
analytics crates. `terminal-ui` now defines typed desktop actions and the engine
presentation boundary. The separate Tauri crate composes the providers/storage
and manages startup, snapshot delivery, and bounded shutdown. It does not depend
on Polars or the quantitative crates. The existing `financial-engine` remains.

SQLite lives in Tauri's application-data directory as `state.sqlite3` (on Windows,
normally under `%APPDATA%/org.financialresearch.terminal/`). Mock/HTTP scopes are
separate; HTTP settings are partitioned by origin, verified organization/user,
and profile. This file stores only desktop preferences and watchlists.
SQLite portfolios and PostgreSQL research services remain backend-owned. Capability
discovery distinguishes the lightweight gateway from the full research service.
Both backends expose portfolio holdings/transactions/performance and JSON export;
the full backend additionally exposes research history and text-only SSE runs, trace details, and adjusted
history/strategy backtests. Research survives navigation and supports explicit
cancellation; the native host joins research workers during shutdown.

Portfolio creation, settings edits, transactions, deletion and reviewed imports
are available through explicit form submissions. Edits/deletion send revisions;
transactions retain their idempotency IDs across retries. Import mode/data changes
invalidate the preview; commit carries the preview hash and expected revision.
Deletion requires the exact portfolio name. Valuation is loaded before the latest
document because the backend may advance its revision when recording a snapshot.
Automatically attached portfolio research context remains disabled; research
sends only text explicitly entered in the composer.
Backend/profile/token changes currently require restarting the native app.

The prior React source and tests are preserved under `frontend/legacy/` for
reference, outside the active TypeScript build and test discovery. Its old
dependencies are not installed by the new manifest. Full reference parity,
trading, detached windows and interactive OIDC login remain later milestones.
Live backend switching remains deferred. Research uses a thread list/conversation
layout, traces use list/detail inspection, and backtests include an equity curve.
Shared tables render at most 100 rows per page. Portfolio editing uses modal
dialogs, preserving failed-write drafts, revision checks and import previews.

## Verification

```powershell
cargo +1.99.0 test --locked --workspace
cargo +1.99.0 clippy --locked --workspace --all-targets -- -D warnings
npm --prefix frontend run build
npm --prefix frontend test
npm --prefix frontend run test:production
cargo +1.99.0 test --manifest-path frontend/src-tauri/Cargo.toml --locked
cargo +1.99.0 clippy --manifest-path frontend/src-tauri/Cargo.toml --locked --all-targets -- -D warnings
```

Browser tests mock Tauri IPC to verify interactions, revision recovery, and native
runtime errors. Rust desktop tests start the actual engine/storage, validate wire
serialization, and verify shutdown. CI retains backend regression coverage and
the Windows desktop checks. Local browser tests can use `PLAYWRIGHT_CHANNEL=msedge`
when the bundled Chromium executable is unavailable.

With the gateway running, verify actual upstream data reaches the Rust engine:

```powershell
cargo +1.99.0 test --manifest-path frontend/src-tauri/Cargo.toml --locked live_provider_reaches_native_engine_snapshot -- --ignored --nocapture
```

This probe uses real provider access and is intentionally excluded from deterministic
CI. Quotes may be delayed or unavailable; displayed source/observation metadata
reflects the provider response.
