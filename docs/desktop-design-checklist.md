# Desktop reference comparison

Reference: installed moomoo **16.34.27008**, inspected on 15 October 2026.
Its Chart, Watchlists, Markets, Options, Calendar and Portfolio Tracker screens
were inspected through the desktop UI. Packaged artwork, fonts and binaries are
not included in this project. The app retains Financial Research Terminal branding.

The [installation audit and product roadmap](moomoo-installation-audit.md) records
the readable installation evidence, original visual specification, capability
matrix, and separately scoped future integrations.

| Reference area | Implemented comparison | Verification |
| --- | --- | --- |
| Desktop chrome | 32px integrated search/title bar, back/forward, native window controls | Browser IPC wiring and standalone Windows build pass; custom minimize/maximize/restore/close verified in a separate mock profile, with prompt process exit. |
| Left navigation | 132px horizontal icon/label rows; 48px collapsed mode; lower Research/Settings; More menu | Navigation/history/persistence browser checks |
| Chart workspace | Narrow watchlist, instrument header, range controls, contextual binding/layout menus; candles/line, local indicators and drawing toolbar | Chart interaction, docking, pinned instruments, independent ranges, drawing editing and failed-save recovery |
| Right tool strip | 36px Quote/News/Calendar tools; closed by default; resize or narrow-screen drawer | Pane state, keyboard resize, focus containment and hidden subscription checks |
| Watchlists | Full-width table, named-list tabs, columns, precise sorting, bulk entry, ordering, local filtering and row context actions | List CRUD, failed writes, atomic preview/commit, saved settings, cursor/selection separation |
| Markets | Overview cards, MY/US/Crypto tabs, ranked views, native numeric filters, configurable columns and named presets | Provider filter serialization, local search, page resets, preset CRUD and identity isolation |
| Options | Mirrored configurable chain, expiry stepping, local filters, contract detail pane/drawer | Exact signed strikes, duplicate contracts, IV percentage, absent quotes, stale/late responses and keyboard containment |
| Calendar | Current-week grid, left date/country/impact filters, table alternative | Feed coverage, undated events, persisted view and selected-instrument earnings |
| Portfolio | Summary metrics and Holdings/Transactions/Performance tabs | Conflict, decimal transaction, reviewed import and deletion fixtures retained |
| News/Research | Headline/detail and conversation layouts; collapsible research activity | Headline provenance, native research cancellation tests |
| Remaining tools | Shared tables, forms, inspectors, empty/error/loading states | Existing native/backend boundaries and regression coverage retained |
| Screeners | Saved-view sidebar, shared Markets presets, native filters and configurable results | Saved views apply to MY/US independently, preserve membership and release hidden owners |
| Bottom bar | Index/watchlist ticker, status disclosure and clock; command overlay on demand | Ticker/view preferences, command history and keyboard tests |

The comparison targets supported app workflows rather than unsupported product
features. No brokerage execution, order book,
social/community integration, detached windows or fabricated provider values are
introduced. Markets results disclose provider coverage; economic events retain
the existing current-week feed and earnings retain selected-instrument coverage.

Deterministic browser screenshots in `frontend/test-results/` cover the desktop,
Markets Overview, Options and Calendar at **1440×1000** and **960×640**, plus
multi-chart layouts. Fixture prices, events and contracts are test data. Live
smoke tests remain read-only and separate from write fixtures.

## Chart/watchlist reference pass

The installed chart was inspected again for its top drawing menu, selected-line
handles, stacked MACD/volume/RSI panes, and moving-average settings sidebar.
The reference separates style (visibility, color, line width) from periods. This
app exposes those supported settings in an accessible indicator dialog, using
original SVG rendering. The reference row menu includes watchlist add/remove and
ordering actions alongside brokerage features; only supported local-list actions
are implemented here.

Local analysis includes SMA, EMA, Bollinger Bands, Wilder RSI and MACD. It uses
loaded unadjusted OHLC, without extra upstream requests. Trend lines, horizontal
levels, rectangles and Fibonacci retracements use UTC/price anchors and are scoped
to panel/instrument. New chart presets copy indicator settings, with fresh panel
IDs and no drawings. Preferences version 7 migrates versions 1–6; SQLite stays at 2.

`chart-analysis.png` and `chart-analysis-small.png` add indicator-pane and drawing
screenshots at both supported sizes. Tests cover warm-up and flat RSI, exact known
MACD/Bollinger results, overlapping drawing creation, handle editing, cancellation,
undo/redo, deletion, range changes, reopening, and failed-save retry. Native tests
cover identity isolation, reopening, failed writes and stale workspace merges.
The standalone native app also committed an indicator change through the scoped
`terminal_chart` capability and displayed “Chart saved”. Direct Cargo standalone
builds enable `--features tauri/custom-protocol` to embed the frontend rather than
depending on a running development server.
The read-only native live-provider probe passed with Yahoo Finance quotes and
124 loaded history candles. Server portfolio/research writes remain covered by
fixtures rather than live mutation tests.

## Markets/options refinement reference

Fresh inspection reached the Markets overview, then desktop automation was stopped
with Escape. The user-supplied screenshots in `pic/` supplied the remaining visual
reference: `Screenshot 2026-10-15 222752.png` (Markets), `222806.png` (Options
underlying/analysis arrangement), and `222911.png` (named screeners and filter/table
hierarchy). The filenames all share the `Screenshot 2026-10-15 ` prefix. Images
were inspected locally; none of their proprietary assets are shipped as UI assets.

Supported equivalents retain compact tabs and controls, table-first inspection,
named local presets, and a separate underlying/contract information area. The
reference's additional exchanges, global map, options analytics and trading
controls are not replicated by this milestone. Markets presets and Options presets
are separate collections, limited to 20 each. MY, US and Crypto display settings
remain independent; selected option contracts and explicit expiry dates are not
stored in presets or preferences.

Additional deterministic screenshots: `markets-custom.png`,
`markets-custom-small.png`, `options-contract.png`, `options-contract-small.png`,
and `options-columns-small.png`. Market test rows and option quotes are fixtures.

Refinement verification: all 34 development and 34 production browser tests passed,
including portfolio, research, chart/drawing and watchlist regressions. Workspace
and native Rust tests, formatting and Clippy passed. Screenshots were reviewed at
1440×1000 and 960×640, including the contract drawer and column dialog. The
read-only native live probe received a Yahoo Finance quote and 124 history candles.

## Installation-driven migration

### Prepared API precision pass — 16 October 2026

Workspace actions now share the 32px title bar. Browsing screens remove redundant
global and instrument headings; page tabs begin immediately below the title bar.
Full-page watchlist columns use explicit widths and aligned instrument/name cells.
Stored column order, density, layouts, and membership remain unchanged.

Markets Overview uses existing native Crypto and Macro resources for crypto
sparklines, commodity ETF proxies, and dated rate observations. Proxy labels do
not imply spot prices; rates do not imply live bond quotes. The typed macro
payload retains backend instrument IDs, exact decimal changes, and currency so
supported proxy cards open instruments without guessing identifiers or adding
watchlist membership. Hidden overview resources release their subscription owners.

Deterministic comparison captures are named `precise-watchlist*` and
`precise-overview*` at 1440×1000 and 960×640. No dependencies, preference versions,
SQLite schemas, providers, or proprietary assets are added. Unsupported order-book,
forex, and trading workflows remain outside this API-backed presentation pass.

Verification: 39 development and 39 production browser tests pass, including
overview owner release and instrument opening without membership changes.
Workspace Rust tests, Clippy, formatting, and frontend production build pass.
Native desktop tests, Clippy, and the standalone Windows executable build pass.
The read-only live probe could not connect to the backend at 127.0.0.1:8000;
the current comparison screenshots use deterministic fixtures, not live quotes.

The original shell uses friendly page names without changing screen identifiers,
semantic selection/rail/table tokens, a 32px contextual header, a compact active
instrument quote, denser tables, and reference-style outlined watchlist cursors.
Markets combines universe navigation and the active Add destination. Screeners
reuses the existing typed market settings and presets, with a 220px saved-view
sidebar on wide windows and a wrapping section on narrow windows. Settings uses
Connection/Keyboard/Workspace section navigation. Shared contextual menus dismiss
with Escape and restore focus; generic tables are keyboard-focusable scroll regions.
Redundant service-page headings are removed without changing their native actions.

Preference version 7, SQLite schema 2, dependencies, providers and saved docking
trees remain unchanged. Additional screenshots use `screener-migration*`,
`migration-stale*`, and `migration-<screen>-1440/960.png` names. Research,
Backtesting and Traces screenshot fixtures intentionally show backend capability
unavailability; their functional native/backend tests remain separate.

The full-page Watchlists view uses one wrapping control strip and no redundant
docking header. Navigation's More popover closes after selecting a screen; its
summary indicates when a secondary screen is active. Browser tests also verify the
native close command alongside minimize/maximize/restore wiring.

Final verification for this migration: 37 development and 37 production browser
tests, workspace/native Rust tests, formatting, Clippy and standalone Windows build
pass. The read-only native live probe returned a Yahoo Finance quote and 124 history
candles. Native window behavior remains covered by the earlier manual desktop pass;
this migration rechecks IPC wiring and native session shutdown, without claiming a
new manual window-control inspection.
