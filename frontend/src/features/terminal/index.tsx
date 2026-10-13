import { useEffect, useRef, useState, lazy, Suspense } from "react";
import { Link, useSearchParams } from "react-router-dom";
import GridLayout, { useContainerWidth } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import { Identity, api, scope } from "../../shared/api";
import { ResearchPage } from "../research/page";
import { Widget, WidgetType, widgetTypes, useTerminal } from "./state";
import { useMarket } from "./market";
import { WidgetSettings, WidgetBoundary } from "./settings";
const ChartWidget = lazy(() =>
  import("./chart").then((module) => ({ default: module.ChartWidget })),
);
const HeatmapWidget = lazy(() =>
  import("./heatmap").then((module) => ({ default: module.HeatmapWidget })),
);
import {
  Quote,
  QuoteWidget,
  WatchlistWidget,
  NewsWidget,
  ScreenerWidget,
  OptionsWidget,
  CryptoWidget,
  MacroWidget,
  CalendarWidget,
} from "./widgets";
const Portfolio = lazy(() =>
  import("../portfolio/page").then((module) => ({
    default: module.PortfolioPage,
  })),
);
function useVisible() {
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    if (!ref.current) return;
    const observer = new IntersectionObserver(
      ([entry]) => setVisible(entry.isIntersecting),
      { rootMargin: "100px" },
    );
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return { ref, visible };
}
function Panel({
  widget,
  identity,
  focused,
  onFocus,
  enabled,
}: {
  widget: Widget;
  identity: Identity;
  focused: boolean;
  onFocus: () => void;
  enabled: boolean;
}) {
  const terminal = useTerminal();
  const asset = widget.linked
    ? terminal.state.active
    : widget.instrument || terminal.state.active;
  const { ref, visible } = useVisible();
  const active = visible && enabled;
  return (
    <div ref={ref} className="terminal-panel">
      <header className="widget-title">
        <span className="widget-drag">
          {widget.type.toUpperCase()}{" "}
          <small>
            {["chart", "quote", "news", "options"].includes(widget.type)
              ? asset.symbol
              : ""}
          </small>
        </span>
        <div className="widget-actions">
          <button
            aria-label={`${widget.linked ? "Pin" : "Link"} ${widget.type}`}
            title={
              widget.linked
                ? "Pin to current instrument"
                : "Follow active instrument"
            }
            onClick={() =>
              terminal.update((old) => ({
                ...old,
                widgets: old.widgets.map((w) =>
                  w.id === widget.id
                    ? { ...w, linked: !w.linked, instrument: asset }
                    : w,
                ),
              }))
            }
          >
            {widget.linked ? "↔" : "⌖"}
          </button>
          <button
            aria-label={`${focused ? "Restore" : "Expand"} ${widget.type}`}
            onClick={onFocus}
          >
            {focused ? "↙" : "↗"}
          </button>
          <button
            aria-label={`Remove ${widget.type} widget`}
            onClick={() => {
              terminal.update((old) => ({
                ...old,
                widgets: old.widgets.filter((w) => w.id !== widget.id),
                layout: old.layout.filter((c) => c.i !== widget.id),
              }));
              if (focused) onFocus();
            }}
          >
            ×
          </button>
        </div>
      </header>
      <div className="widget-body">
        <WidgetSettings widget={widget}>
          <WidgetBoundary key={asset.id + widget.type}>
            <Suspense fallback={<p>Loading panel…</p>}>
              {widget.type === "chart" ? (
                <ChartWidget instrument={asset} active={active} />
              ) : widget.type === "quote" ? (
                <QuoteWidget instrument={asset} active={active} />
              ) : widget.type === "watchlist" ? (
                <WatchlistWidget active={active} />
              ) : widget.type === "news" ? (
                <NewsWidget instrument={asset} active={active} />
              ) : widget.type === "screener" ? (
                <ScreenerWidget active={active} onPick={terminal.select} />
              ) : widget.type === "heatmap" ? (
                <HeatmapWidget active={active} onPick={terminal.select} />
              ) : widget.type === "options" ? (
                <OptionsWidget
                  key={asset.id}
                  instrument={asset}
                  active={active}
                />
              ) : widget.type === "crypto" ? (
                <CryptoWidget active={active} onPick={terminal.select} />
              ) : widget.type === "macro" ? (
                <MacroWidget active={active} />
              ) : widget.type === "calendar" ? (
                <CalendarWidget active={active} />
              ) : (
                <Suspense fallback={<p>Loading portfolio…</p>}>
                  <Portfolio identity={identity} />
                </Suspense>
              )}
            </Suspense>
          </WidgetBoundary>
        </WidgetSettings>
      </div>
    </div>
  );
}
export function TerminalPage({ identity }: { identity: Identity }) {
  const terminal = useTerminal(),
    [params, setParams] = useSearchParams();
  const [focus, setFocus] = useState<string | null>(null),
    [adding, setAdding] = useState(false),
    [privateContext, setPrivateContext] = useState<Record<
      string,
      unknown
    > | null>(null),
    [contextError, setContextError] = useState("");
  const { width, containerRef, mounted } = useContainerWidth({
    measureBeforeMount: true,
    initialWidth: 0,
  });
  const mobile = width > 0 && width < 700;
  const quote = useMarket<Quote>(
    `/market/quote?instrument=${encodeURIComponent(terminal.state.active.id)}`,
    15000,
    terminal.state.researchOpen && !focus,
  );
  useEffect(() => {
    if (
      params.get("widget") === "portfolio" &&
      !terminal.state.widgets.some((w) => w.type === "portfolio")
    )
      terminal.add("portfolio");
    if (params.get("research"))
      terminal.update((old) => ({ ...old, researchOpen: true }));
    if (params.get("focus")) setFocus(params.get("focus"));
  }, [params.toString()]);
  useEffect(() => {
    function key(event: KeyboardEvent) {
      if (!event.altKey || event.ctrlKey || event.metaKey || event.repeat)
        return;
      const types: WidgetType[] = [
        "chart",
        "quote",
        "news",
        "screener",
        "heatmap",
        "crypto",
        "options",
        "portfolio",
      ];
      const digit = Number(event.key);
      if (digit >= 1 && digit <= 9) {
        event.preventDefault();
        if (digit === 9) toggleResearch();
        else terminal.add(types[digit - 1]);
      }
    }
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [terminal]);
  const focusedWidget = terminal.state.widgets.find(
    (w) => w.id === focus || w.type === focus,
  );
  const context: Record<string, unknown> = {
    instrument: terminal.state.active,
    ...(quote?.data?.instrument.id === terminal.state.active.id
      ? {
          quote: {
            price: quote.data.price,
            currency: quote.data.currency,
            observed_at: quote.observed_at,
            source: quote.source,
            stale: quote.stale,
            fetched_at: quote.fetched_at,
          },
        }
      : {}),
    ...(privateContext ? { portfolio: privateContext } : {}),
  };
  async function includePortfolio() {
    setContextError("");
    if (privateContext) {
      setPrivateContext(null);
      return;
    }
    try {
      const key = localStorage.getItem(
        `portfolio:${identity.org}:${identity.user}`,
      );
      if (!key)
        throw new Error(
          "Open a portfolio widget and select a saved portfolio first.",
        );
      const document = await api<{
        portfolio: {
          name: string;
          currency: string;
          positions: Record<string, unknown>[];
        };
        id: string;
      }>(`/portfolio?${scope(identity, key)}`);
      setPrivateContext({
        portfolio_id: key,
        name: document.portfolio.name,
        currency: document.portfolio.currency,
        holding_count: document.portfolio.positions.length,
        omitted_count: Math.max(0, document.portfolio.positions.length - 20),
        positions: document.portfolio.positions.slice(0, 20).map((p) => ({
          symbol: p.symbol,
          quantity: p.quantity,
          average_cost: p.average_cost,
        })),
      });
    } catch (error) {
      setContextError((error as Error).message);
    }
  }
  useEffect(() => {
    if (
      focus &&
      focus !== "research" &&
      !focusedWidget &&
      focus !== "portfolio"
    )
      clearFocus();
  }, [focus, focusedWidget]);
  function toggleResearch() {
    const visible =
      !focusedWidget && (terminal.state.researchOpen || focus === "research");
    if (focus) clearFocus();
    terminal.update((old) => ({ ...old, researchOpen: !visible }));
  }
  function clearFocus() {
    setFocus(null);
    const next = new URLSearchParams(params);
    next.delete("focus");
    next.delete("widget");
    next.delete("research");
    setParams(next, { replace: true });
  }
  return (
    <main className="terminal-page">
      <div className="terminal-toolbar">
        <div>
          <b>{terminal.state.active.symbol}</b>
          <small>
            {terminal.state.active.exchange} · {terminal.state.active.name}
          </small>
        </div>
        <button onClick={() => setAdding((old) => !old)}>+ Add widget</button>
        <button onClick={toggleResearch}>
          {!focusedWidget &&
          (terminal.state.researchOpen || focus === "research")
            ? "Hide"
            : "Open"}{" "}
          Research
        </button>
        {focus && <button onClick={clearFocus}>Restore workspace</button>}
        <button
          onClick={() => {
            terminal.reset();
            clearFocus();
          }}
        >
          Reset layout
        </button>
      </div>
      {adding && (
        <div className="widget-menu">
          {widgetTypes.map((type) => (
            <button
              key={type}
              onClick={() => {
                terminal.add(type);
                setAdding(false);
              }}
            >
              {type}
            </button>
          ))}
          <small>Alt+1–8 add panels · Alt+9 Research</small>
        </div>
      )}
      {terminal.storageError && <p role="alert">{terminal.storageError}</p>}
      <div
        className={`terminal-content ${focus === "research" ? "research-focused" : ""}`}
      >
        <div
          ref={containerRef}
          className="terminal-grid"
          hidden={focus === "research"}
        >
          {!terminal.state.widgets.length && (
            <p>Add a widget to begin. Your portfolio records are unchanged.</p>
          )}
          {mounted && (
            <GridLayout
              width={width || 1000}
              layout={
                mobile
                  ? terminal.state.layout.map((cell, index) => ({
                      ...cell,
                      x: 0,
                      y: terminal.state.layout
                        .slice(0, index)
                        .reduce((sum, c) => sum + c.h, 0),
                      w: 1,
                    }))
                  : terminal.state.layout
              }
              gridConfig={{
                cols: mobile ? 1 : 12,
                rowHeight: 28,
                margin: [8, 8],
                containerPadding: [0, 0],
              }}
              dragConfig={{
                handle: ".widget-drag",
                cancel: "button,input,select,textarea",
                enabled: !focus && !mobile,
              }}
              resizeConfig={{ enabled: !focus && !mobile, handles: ["se"] }}
              onLayoutChange={(layout) => {
                if (!focus && !mobile)
                  terminal.update((old) => ({
                    ...old,
                    layout: layout.map(({ i, x, y, w, h }) => ({
                      i,
                      x,
                      y,
                      w,
                      h,
                    })),
                  }));
              }}
            >
              {terminal.state.widgets.map((widget) => (
                <div
                  key={widget.id}
                  className={
                    focusedWidget
                      ? focusedWidget.id === widget.id
                        ? "widget-focused"
                        : "widget-hidden"
                      : ""
                  }
                >
                  <Panel
                    widget={widget}
                    identity={identity}
                    enabled={!focus || focusedWidget?.id === widget.id}
                    focused={focusedWidget?.id === widget.id}
                    onFocus={() =>
                      focusedWidget?.id === widget.id
                        ? clearFocus()
                        : setFocus(widget.id)
                    }
                  />
                </div>
              ))}
            </GridLayout>
          )}
        </div>
        <aside
          className={`research-dock ${focus === "research" ? "expanded" : ""}`}
          hidden={
            Boolean(focusedWidget) ||
            (!terminal.state.researchOpen && focus !== "research")
          }
          aria-label="Research sidebar"
        >
          <header className="widget-title">
            <b>DEEP AGENTS RESEARCH</b>
            <div className="widget-actions">
              <Link to="/traces">Traces</Link>
              <button
                aria-label="Expand research sidebar"
                onClick={() =>
                  setFocus((old) => (old === "research" ? null : "research"))
                }
              >
                ↗
              </button>
              <button
                aria-label="Close research sidebar"
                onClick={() => {
                  terminal.update((old) => ({ ...old, researchOpen: false }));
                  if (focus === "research") clearFocus();
                }}
              >
                ×
              </button>
            </div>
          </header>
          <div className="research-context">
            <span>
              Context: {terminal.state.active.symbol} · public market snapshot
            </span>
            <button onClick={() => void includePortfolio()}>
              {privateContext
                ? "Exclude portfolio context"
                : "Include selected portfolio"}
            </button>
            {privateContext && (
              <small>
                Includes {String(privateContext.name)} holdings in this request.
              </small>
            )}
            {contextError && <p role="alert">{contextError}</p>}
          </div>
          <ResearchPage
            identity={identity}
            embedded
            terminalContext={context}
          />
        </aside>
      </div>
    </main>
  );
}
