# Moomoo installation audit and original frontend specification

Inspected 15 October 2026. Reference installation:
`C:\Program Files\moomoo\app\16.34.27008`.
The executable reports product/file version **16.34.27008.10080003**.
The installation was read only. This document describes observed resources and
independently implemented equivalents; it is not a recovered source tree.

## Evidence and architecture mapping

| Observation | Established fact | Mapping to this terminal |
| --- | --- | --- |
| Qt5Core/Gui/Widgets/Network/Sql/Svg libraries | The installation ships Qt 5 components | Retain Tauri native chrome and React; do not reproduce its runtime stack |
| libcef, FTWebRender, Chromium resource packs | Chromium embedding components are present | React uses the existing Tauri webview and typed IPC |
| FTQuote, QotBase, F3CChart, FinancialSDK modules | Separate modules with these names exist | Quote scheduling stays in terminal-engine; SVG charting stays in the frontend. Exact internal responsibility is unverified |
| Data/DefaultPanelLayout.json | Five default references: Active Trader, Options Trader, Portfolio Tracker, Multi Chart, and Price Placed Trade | Keep original named workspace presets; include only supported panels |
| Data/LayoutTemplates/*.ftl | Eight files exist; MultiChart.ftl contains binary serialization and type markers | Do not import or deserialize reference layouts; retain the bounded typed docking tree |
| Data/GridviewCfg.xml | 572 column definitions; early definitions specify symbol/name/last widths of 80/160/84 | Use independent typed column IDs and measured table proportions; no wholesale column import |
| Data/QotGridViewCfg.xml | 220 GridView definitions and 5,787 column occurrences | Support local column visibility, order, sorting and presets without assuming all reference fields are available |
| skins/*.rcc | Compiled black, white and common resource bundles | Use original CSS tokens; no theme extraction or asset distribution |
| Web directory | Two HTML, two CSS and seven JS files | Presence does not establish that all product pages are web based |
| Web/Webview/clientjssdk.js | Readable bridge wrapper references native message dispatch and browser request interception | Keep explicit typed Tauri commands; do not copy its bridge or interception implementation |
| TradeBusiness, FTQuant, FTNews, FTSocial modules | Modules with these names are installed | Product categories inform the roadmap only; filenames do not prove APIs, entitlement or capabilities |

No private profile directories, account sessions, stock database contents, network
protocols, credentials or account endpoints were inspected. No packaged source,
artwork, font, binary, database or template is included in the application.

## Visual and interaction specification

Reference images are the user-supplied screenshots in `pic/`. All names start with
`Screenshot 2026-10-15 `: `222752` Markets, `222806` Options analysis/underlying,
`222824` Watchlists, `222833` Calendar, `222844` templates, `222852` Discover,
`222901` News, `222911` Screener, `222923` Algo, `222933` multi-chart and `222946`
paper trading. The latter screens supply layout evidence, not authorization for
account integrations. Personal values in reference images are not transcribed.

| Element | Original application specification |
| --- | --- |
| Shell | 32px title/search bar with workspace actions, 132px rail with 40px icon/label rows, 48px collapsed rail, page tabs immediately below title bar on browsing screens, 36px information strip, 26px footer |
| Palette | Surface #13161C, raised #1A1E26, rail #252B36, primary #E1EAFA, secondary #A8B3C6, orange #EA670C, green #00A357, red #E02D48, blue #4E7FCE |
| Type | System sans serif at 13px; 12px table cells and 11px secondary controls; tabular monospace values; no installed fonts copied |
| Hierarchy | One page title; page-specific tabs and compact contextual tools. Instrument header shows selected symbol, available quote, change, currency and stale/unavailable state |
| Tables | Compact 28px baseline rows, comfortable 38px baseline; multi-line cells may grow. Sticky headers, internal horizontal scrolling, explicit unavailable values; sorting never rewrites membership |
| Selection | Muted filled active row, light outline for watchlist cursor, distinct orange navigation and action emphasis; cursor movement and instrument selection remain separate |
| Markets | Universe tabs and active Add destination share a row; supported index cards and grouped provider tables. No artificial map, extra instruments or claimed exhaustive coverage |
| Screeners | 220px saved-view sidebar on wide windows, wrapping top section below 1200px; existing market presets, filters, columns and provider pagination reused |
| Chart | Existing range/style/indicator/drawing controls, linked/pinned bindings and shared time axis retained. Saved docking ratios override default arrangements |
| Options | Mirrored calls/strike/puts and existing detail/underlying pane; provider expiries, exact decimal strikes, duplicate contracts and optional fields preserved |
| Settings | Section navigation for Connection, Keyboard and Workspace; all controls remain accessible. No credentials enter the frontend |
| Dialogs | Restrained borders and small corner radius, responsive width, scrollable body, visible focus and contained keyboard navigation |
| Menus | Outside pointer dismisses contextual popovers; Escape closes the focused menu and restores its summary focus. Open modal dialogs retain their own Escape handling |
| Data states | Native-owned loading/error/retry behavior, stale retained values, failed-write drafts and persistent mock banner remain authoritative |

Sizes are implementation targets derived from the screenshots and existing
desktop constraints, not claims of recovered internal constants.

## Feature matrix

| Area | Status after migration | Remaining evidence or capability gap |
| --- | --- | --- |
| Chrome/navigation/footer | Implemented | Native controls were verified in the earlier desktop pass; current browser tests exercise the same IPC wiring |
| Chart/indicators/drawings | Implemented supported equivalents | Chart context-menu placement and additional reference shortcuts need manual evidence |
| Named watchlists | Implemented | Intraday sparkline per member would require additional history ownership and workload analysis |
| Markets | Implemented supported index cards, rankings and tables | Global market map, additional markets and asset groups require provider coverage review |
| Screeners | Implemented shared filters, saved views, columns, page reset and separate open/add actions | Fundamental predicates visible in reference are absent from the current screener contract |
| Options | Implemented chain, filters, presets, exact strike handling and details | Reference image shows analysis, not chain interaction; advanced analytics are roadmap work |
| Calendar | Implemented current-week economics and selected-instrument earnings | No arbitrary-date or market-wide earnings claim |
| Portfolio | Implemented existing holdings, transactions, performance, revisions and import review | Broker account aggregation and reference-specific return metrics require integrations |
| News/Research | Implemented existing list/detail and conversation/activity layouts | No community feed; full research requires the existing full backend |
| Crypto/Macro/Backtesting/Traces/Settings | Existing workflows retained with shared presentation | Backtesting and traces have no corresponding supplied reference screen; avoid invented parity claims |

## Full product roadmap — planning only

Order reflects dependencies, not a commitment to deliver these integrations in this
migration. Every new provider or account capability requires a separate design.

| Sequence / capability | User workflow | Data and integration requirement | Persistence impact | Acceptance gate |
| --- | --- | --- | --- | --- |
| 1. Fundamentals and richer screening | Inspect financial statements/ratios; save multi-predicate screens | Verify existing analytics coverage, then add supported backend fields; define fiscal period and units | Versioned predicate definitions and local views | Known statement fixtures, units/period provenance, missing values and provider filtering agree |
| 2. Option analytics | Inspect aggregate volume/OI and volatility context | Loaded-chain aggregation may use current fields; Greeks, historical IV and payoff require separately specified models/data | Analysis settings only; provider expiry remains session local | Exact contract accounting, missing-field coverage, numerical model fixtures; no inferred missing quotes |
| 3. Alerts | Create price/percentage conditions and review triggers | Define polling freshness, market sessions and delivery channel; background operation needs native scheduling design | Durable definitions, enabled state and deduplicated trigger records | Restart/reconnect, stale suppression and exactly-once trigger presentation verified |
| 4. Broader markets and depth | Browse more exchanges, time/sales and order books | Licensed or documented provider integration with exchange entitlements, timestamps and sequencing | Provider configuration; bounded caches, not account secrets | Entitlement failures, sequence gaps and stale depth explicit; no fabricated levels |
| 5. Paper trading | Submit simulated orders and inspect cash/positions/history | Independent simulation ledger and specified fill/slippage/session rules; quotes alone cannot establish executable fills | Separate simulation accounts, orders and immutable executions | Deterministic fills, decimal cash conservation, restart/retry safety, permanent simulation labeling |
| 6. Brokerage execution | Connect a broker, review and submit orders, reconcile fills | Documented broker API, explicit account consent, order lifecycle and reconciliation design | Native credential vault, account-scoped audit/order records | Sandbox qualification, rejection/cancel/partial-fill/idempotency tests and separately approved live rollout |
| 7. Community/social | Read/publish posts, follow discussions | Authorized service, content and identity APIs, moderation/reporting policies | Account-scoped content caches and user preferences | Privacy, moderation, publishing confirmation and offline/error behavior verified |
| 8. Algo and detached workspaces | Configure strategies or separate windows | Review existing backtest engine; live automation and multi-window ownership need separate specifications | Versioned strategy configuration and per-window layout ownership | Cancel/restart correctness, shared-subscription ownership and risk boundaries demonstrated |

## Verification and evidence limits

Each UI stage remains runnable without migrations. Preference version **7** and
SQLite schema **2** remain unchanged. Existing identities, custom layouts, panel
IDs, drawings, lists, keymaps and decimal wire values are retained.

Compare deterministic screenshots at 1440×1000 and 960×640 for expanded/collapsed
rails, saved screeners, column dialogs, contract drawers, tables, empty states,
stale values and multi-chart layouts. Run development/production browser suites,
workspace/native Rust tests, formatting, Clippy and the embedded Windows build.
Live provider checks are read only; portfolio writes use fixtures.

Remaining manual evidence: exact reference chain keyboard behavior, menu dismissal,
column-resizing gestures, template transitions, modal focus behavior, and display
scaling at non-default DPI. Supplied screenshots cannot establish those behaviors.
No binary decompilation, private protocol replication or moomoo account access is
part of this deliverable.
