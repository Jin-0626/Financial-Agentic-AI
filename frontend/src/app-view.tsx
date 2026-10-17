import { BrowserSettingsProvider } from "./browser-settings";
import { StockLabel } from "./stock-label";
import { DesktopViewProvider } from "./view-settings";
import { TitleBar, TitleBarTools } from "./title-bar";
import { MarketTicker } from "./index-market";
import { defaultViews } from "./bridge";
import { Panel } from "./panel";
import { panelSpec } from "./bridge";
import type { AppState } from "./main";
import { Shell } from "./shell";
import { SymbolSearch } from "./symbol-search";
import { MarketDesk } from "./market-desk";
import { MarketChart } from "./chart";
import { QuoteOwners } from "./subscriptions";
import { MarketsBrowser, ScreenerBrowser } from "./markets-browser";
import { Watchlist } from "./watchlist";
import { DockWorkspace } from "./docking";
import { FeatureScreen, MarketResource } from "./resources";
import { SettingsScreen } from "./settings";
import { price, numeric, defaultWorkspace, type Screen } from "./bridge";
export function AppView({ state }: { state: AppState }) {
  const {
    view,
    setView,
    compact,
    setCompact,
    snapshot,
    error,
    workspace,
    dock,
    symbols,
    selected,
    quote,
    change,
    current,
    latestBar,
    candles,
    cursor,
    setCursor,
    cursorRef,
    send,
    saveWorkspace,
    saveDock,
    addPanel,
    applyTemplate,
    addInput,
    layoutName,
    setLayoutName,
    palette,
    setPalette,
    paletteQuery,
    setPaletteQuery,
    commandOpen,
    setCommandOpen,
    command,
    setCommand,
    historyKey,
    historyIndex,
    prompt,
    uptime,
    setConnectionAttempt,
    newSymbol,
    setNewSymbol,
    sort,
    setSort,
    descending,
    setDescending,
    layoutPanels,
    updatePanel,
  } = state;
  const standaloneId =
    Object.entries(layoutPanels).find(
      ([, spec]) => spec.kind === "watchlist",
    )?.[0] ?? "watchlist";
  const definitions =
    view === "watchlist"
      ? {
          [standaloneId]: {
            ...(layoutPanels[standaloneId] ?? panelSpec("watchlist")),
            watchlist_id: snapshot?.preferences.active_watchlist_id ?? "main",
          },
        }
      : layoutPanels;
  const panelOwner =
    view === "dashboard"
      ? `layout:${workspace.active_layout}`
      : `screen:${view}`;
  const desktop = workspace.desktop ?? defaultViews();
  const navigate = (screen: Screen) => {
    setView(screen);
    saveWorkspace({ screen });
  };
  return (
    <BrowserSettingsProvider saved={workspace.browser}>
      <DesktopViewProvider
        view={desktop}
        onChange={(view) => void send({ type: "view", view })}
      >
        <Shell
          screen={view}
          compact={compact}
          onNavigate={(screen) => {
            setView(screen);
            saveWorkspace({ screen });
          }}
          header={
            <TitleBar
              screen={view}
              enabled={snapshot?.mode === "HTTP"}
              onNavigate={navigate}
              onSelect={(id) => {
                void send({ type: "select", symbol: id });
                navigate("dashboard");
              }}
            />
          }
          toolbar={
            <>
              {" "}
              <TitleBarTools>
                <div className="toolbar-controls">
                  <button
                    onClick={() => setPalette(true)}
                    aria-label="Open command palette"
                  >
                    Ctrl K
                  </button>
                  <button
                    aria-label="Save workspace"
                    disabled={!snapshot}
                    onClick={() => void send({ type: "save" })}
                  >
                    <svg viewBox="0 0 24 24" aria-hidden="true">
                      <path d="M4 3h13l4 4v14H3V3z M7 3v6h10V3 M7 21v-8h10v8" />
                    </svg>
                  </button>
                  <details className="workspace-tools">
                    <summary
                      aria-label="Workspace tools"
                      title="Workspace tools"
                    >
                      <svg viewBox="0 0 24 24" aria-hidden="true">
                        <path d="M3 6h18 M3 12h18 M3 18h18 M8 3v6 M16 9v6 M10 15v6" />
                      </svg>
                    </summary>
                    <div>
                      <p className="workspace-connection">
                        <span className="connection-state">
                          {snapshot?.mode ?? "CONNECTING"}
                        </span>{" "}
                        · {symbols.length} active-list instruments
                      </p>
                      <select
                        aria-label="Add dashboard panel"
                        value=""
                        disabled={!snapshot || view !== "dashboard"}
                        onChange={(event) => addPanel(event.target.value)}
                      >
                        <option value="">+ Panel</option>
                        {[
                          "chart",
                          "watchlist",
                          "news",
                          "macro",
                          "calendar",
                          "screener",
                        ].map((panel) => (
                          <option key={panel}>{panel}</option>
                        ))}
                      </select>
                      <select
                        aria-label="Saved layout"
                        value={workspace.active_layout}
                        disabled={!snapshot}
                        onChange={(event) =>
                          saveWorkspace({ active_layout: event.target.value })
                        }
                      >
                        {workspace.layouts.map((layout) => (
                          <option key={layout.name}>{layout.name}</option>
                        ))}
                      </select>
                      <select
                        aria-label="Dashboard template"
                        value=""
                        disabled={!snapshot || workspace.layouts.length >= 12}
                        onChange={(event) => applyTemplate(event.target.value)}
                      >
                        <option value="">Templates</option>
                        {[
                          " Market Desk",
                          "Two Chart Desk",
                          "Four Chart Desk",
                          "Market Overview",
                          "Portfolio Research",
                          "Screening Desk",
                        ].map((name) => (
                          <option key={name}>{name}</option>
                        ))}
                      </select>
                      <details className="layout-menu">
                        <summary>Layouts</summary>
                        <div>
                          <label>
                            Name
                            <input
                              aria-label="Layout name"
                              maxLength={64}
                              value={layoutName}
                              onChange={(event) =>
                                setLayoutName(event.target.value)
                              }
                            />
                          </label>
                          <button
                            disabled={
                              !snapshot ||
                              !layoutName.trim() ||
                              workspace.layouts.length >= 12 ||
                              workspace.layouts.some(
                                (layout) => layout.name === layoutName.trim(),
                              )
                            }
                            onClick={() => {
                              const name = layoutName.trim();
                              saveWorkspace({
                                active_layout: name,
                                layouts: [
                                  ...workspace.layouts,
                                  { name, root: dock, panels: layoutPanels },
                                ],
                              });
                              setLayoutName("");
                            }}
                          >
                            Save as
                          </button>
                          <button
                            disabled={!snapshot}
                            onClick={() =>
                              saveDock(defaultWorkspace().layouts[0].root)
                            }
                          >
                            Reset panels
                          </button>
                          <button
                            disabled={!snapshot || workspace.layouts.length < 2}
                            onClick={() => {
                              const layouts = workspace.layouts.filter(
                                (layout) =>
                                  layout.name !== workspace.active_layout,
                              );
                              saveWorkspace({
                                layouts,
                                active_layout: layouts[0].name,
                              });
                            }}
                          >
                            Delete layout
                          </button>
                        </div>
                      </details>
                      <button
                        aria-pressed={compact}
                        onClick={() => {
                          setCompact(!compact);
                          saveWorkspace({ compact: !compact });
                        }}
                      >
                        COMPACT
                      </button>
                      <button
                        disabled={!snapshot}
                        onClick={() => {
                          setView("dashboard");
                          requestAnimationFrame(() =>
                            addInput.current?.focus(),
                          );
                        }}
                      >
                        + SYMBOL
                      </button>
                    </div>
                  </details>
                </div>
              </TitleBarTools>
              {snapshot?.mode === "MOCK" && (
                <div className="notice mock-notice">
                  <span className="notice-mark">◇</span>Synthetic market data ·
                  For development and exploration only
                </div>
              )}
            </>
          }
          footer={
            <>
              {" "}
              {snapshot && (
                <QuoteOwners
                  ids={[
                    ...new Set([
                      ...(desktop.ticker_mode === "watchlist"
                        ? snapshot.watchlist.symbols
                        : []),
                      ...(snapshot.preferences.selected
                        ? [snapshot.preferences.selected]
                        : []),
                    ]),
                  ]}
                  owner="workspace:active"
                />
              )}
              <form
                className={`command-bar ${commandOpen ? "open" : ""}`}
                aria-hidden={!commandOpen}
                onSubmit={(event) => {
                  event.preventDefault();
                  if (command.trim()) {
                    void send({ type: "command", text: command });
                    setCommand("");
                    setCommandOpen(false);
                    historyIndex.current = null;
                  }
                }}
              >
                <span className="command-label">COMMAND</span>
                <span className="prompt-chevron">›</span>
                <input
                  ref={prompt}
                  aria-label="Command prompt"
                  placeholder=":add AAPL · :del AAPL · :w save · :q quit"
                  value={command}
                  maxLength={1024}
                  onChange={(event) => {
                    setCommand(event.target.value);
                    historyIndex.current = null;
                  }}
                  onKeyDown={historyKey}
                  disabled={!snapshot || !commandOpen}
                />
                <button
                  type="button"
                  aria-label="Close command prompt"
                  onClick={() => setCommandOpen(false)}
                >
                  ×
                </button>
              </form>
              <footer className="desktop-footer">
                {snapshot && (
                  <MarketTicker
                    snapshot={snapshot}
                    onSelect={(id) => void send({ type: "select", symbol: id })}
                  />
                )}
                <details className="footer-status">
                  <summary
                    className={error ? "negative" : ""}
                    role={error ? "alert" : "status"}
                  >
                    {error ?? snapshot?.message ?? "Native engine required"}
                  </summary>
                  <div>
                    <p>
                      {snapshot?.mode ?? "Disconnected"} ·{" "}
                      {snapshot?.latency_ms ?? "—"} ms · {uptime}s active
                    </p>
                    <p>SQLite · local identity-scoped preferences</p>
                  </div>
                </details>
                <button
                  aria-label="Open command prompt"
                  onClick={() => {
                    setCommandOpen(true);
                    requestAnimationFrame(() => prompt.current?.focus());
                  }}
                >
                  :
                </button>
                <time>
                  {new Date().toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit",
                  })}
                </time>
              </footer>
            </>
          }
          overlay={
            palette && (
              <div
                className="palette-backdrop"
                onClick={() => setPalette(false)}
              >
                <section
                  className="command-palette"
                  role="dialog"
                  aria-modal="true"
                  aria-label="Command palette"
                  onClick={(event) => event.stopPropagation()}
                  onKeyDown={(event) => {
                    if (event.key !== "Tab") return;
                    const controls = [
                      ...event.currentTarget.querySelectorAll<HTMLElement>(
                        "input, button:not(:disabled), select",
                      ),
                    ];
                    const first = controls[0],
                      last = controls[controls.length - 1];
                    if (event.shiftKey && document.activeElement === first) {
                      event.preventDefault();
                      last?.focus();
                    }
                    if (!event.shiftKey && document.activeElement === last) {
                      event.preventDefault();
                      first?.focus();
                    }
                  }}
                >
                  <input
                    autoFocus
                    aria-label="Search commands"
                    placeholder="Screen, symbol, layout or panel…"
                    value={paletteQuery}
                    onChange={(event) => setPaletteQuery(event.target.value)}
                  />
                  <button onClick={() => setPalette(false)}>Close</button>
                  <div className="palette-results">
                    {(
                      [
                        "dashboard",
                        "markets",
                        "watchlist",
                        "crypto",
                        "portfolio",
                        "research",
                        "news",
                        "screener",
                        "options",
                        "macro",
                        "calendar",
                        "backtesting",
                        "traces",
                        "settings",
                      ] as Screen[]
                    )
                      .filter((screen) =>
                        screen.includes(paletteQuery.toLowerCase()),
                      )
                      .map((screen) => (
                        <button
                          key={screen}
                          onClick={() => {
                            setView(screen);
                            saveWorkspace({ screen });
                            setPalette(false);
                          }}
                        >
                          Open {screen}
                        </button>
                      ))}
                    {symbols
                      .filter((id) =>
                        id.toLowerCase().includes(paletteQuery.toLowerCase()),
                      )
                      .map((id) => (
                        <button
                          key={id}
                          onClick={() => {
                            void send({ type: "select", symbol: id });
                            setPalette(false);
                          }}
                        >
                          Select{" "}
                          <StockLabel
                            code={id}
                            name={snapshot?.quotes[id]?.data?.instrument.name}
                          />
                        </button>
                      ))}
                    {workspace.layouts
                      .filter((layout) =>
                        layout.name
                          .toLowerCase()
                          .includes(paletteQuery.toLowerCase()),
                      )
                      .map((layout) => (
                        <button
                          key={layout.name}
                          onClick={() => {
                            saveWorkspace({
                              active_layout: layout.name,
                              screen: "dashboard",
                            });
                            setPalette(false);
                          }}
                        >
                          Layout {layout.name}
                        </button>
                      ))}
                  </div>
                </section>
              </div>
            )
          }
        >
          {" "}
          {!snapshot && (
            <section className="startup">
              <h2>
                {error
                  ? "The engine could not start"
                  : "Starting your market desk"}
              </h2>
              <p>
                {error ??
                  "Opening local storage and preparing market subscriptions…"}
              </p>
              {error && (
                <button
                  className="save"
                  onClick={() => setConnectionAttempt((attempt) => attempt + 1)}
                >
                  Retry connection
                </button>
              )}
            </section>
          )}
          {snapshot && (
            <>
              {view === "settings" ? (
                <SettingsScreen snapshot={snapshot} />
              ) : view !== "dashboard" &&
                view !== "markets" &&
                view !== "watchlist" &&
                view !== "screener" ? (
                <MarketDesk snapshot={snapshot}>
                  <FeatureScreen
                    key={view}
                    screen={view}
                    selected={selected ?? null}
                    mode={snapshot.mode}
                    onSelect={(id) => {
                      void send({ type: "select", symbol: id });
                      setView("markets");
                      saveWorkspace({ screen: "markets" });
                    }}
                  />
                </MarketDesk>
              ) : view === "screener" ? (
                <MarketDesk snapshot={snapshot}>
                  <ScreenerBrowser
                    snapshot={snapshot}
                    onSelect={(id) => void send({ type: "select", symbol: id })}
                  />
                </MarketDesk>
              ) : view === "markets" ? (
                <MarketDesk snapshot={snapshot}>
                  <MarketsBrowser
                    snapshot={snapshot}
                    onSelect={(id) => void send({ type: "select", symbol: id })}
                  />
                </MarketDesk>
              ) : (
                <MarketDesk snapshot={snapshot}>
                  <DockWorkspace
                    root={
                      view === "watchlist"
                        ? {
                            type: "tabs",
                            id: "watchlist",
                            panels: [standaloneId],
                            active: standaloneId,
                          }
                        : dock
                    }
                    onChange={view === "dashboard" ? saveDock : () => {}}
                    panels={Object.fromEntries(
                      Object.entries(definitions).map(([id, spec]) => [
                        id,
                        <Panel
                          key={id}
                          id={id}
                          spec={spec}
                          owner={`${panelOwner}:${id}`}
                          snapshot={snapshot}
                          editable={view === "dashboard"}
                          onSelect={(id) => {
                            void send({ type: "select", symbol: id });
                          }}
                          onGlobalRange={(range) =>
                            void send({ type: "set_range", range })
                          }
                          onChange={(value) => updatePanel(id, value)}
                          watchlist={
                            <Watchlist
                              snapshot={snapshot}
                              fullPage={view === "watchlist"}
                              spec={spec}
                              owner={`${panelOwner}:${id}`}
                              onChange={(value) => updatePanel(id, value)}
                              onSelect={(id, index) => {
                                cursorRef.current = index;
                                setCursor(index);
                                void send({ type: "select", symbol: id });
                              }}
                              inputRef={addInput}
                            />
                          }
                        />,
                      ]),
                    )}
                    titles={Object.fromEntries(
                      Object.entries(definitions).map(([id, spec]) => [
                        id,
                        spec.kind,
                      ]),
                    )}
                  />
                </MarketDesk>
              )}
            </>
          )}
        </Shell>
      </DesktopViewProvider>
    </BrowserSettingsProvider>
  );
}
