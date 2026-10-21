import {
  StrictMode,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createRoot } from "react-dom/client";
import {
  type Action,
  type Snapshot,
  connect,
  dispatch,
  numeric,
  ranges,
  defaultWorkspace,
  DEFAULT_LAYOUT_NAME,
  normalizeWorkspace,
  panelSpec,
  panelIds,
  type PanelKind,
  type PanelSpec,
  type Range,
  type Workspace,
  type DockNode,
  type Screen,
} from "./bridge";
import { StockNames } from "./stock-label";
import { AppView } from "./app-view";
import { sortSymbols, type WatchlistSort } from "./watchlist";
import "./style.css";
import "./theme.css";
import "./desktop.css";
export function useAppState() {
  const [view, setView] = useState<Screen>("dashboard");
  const [compact, setCompact] = useState(false);
  const [connectionAttempt, setConnectionAttempt] = useState(0);
  const [layoutName, setLayoutName] = useState("");
  const [palette, setPalette] = useState(false),
    [paletteQuery, setPaletteQuery] = useState("");
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPalette((value) => !value);
      }
      if (event.key === "Escape") setPalette(false);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  const addInput = useRef<HTMLInputElement>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null),
    [error, setError] = useState<string | null>(null);
  const [commandOpen, setCommandOpen] = useState(false);
  const [command, setCommand] = useState(""),
    [newSymbol, setNewSymbol] = useState(""),
    [cursor, setCursor] = useState(0),
    [uptime, setUptime] = useState(0);
  const prompt = useRef<HTMLInputElement>(null),
    started = useRef(performance.now()),
    historyIndex = useRef<number | null>(null),
    draft = useRef(""),
    cursorRef = useRef(0);
  const send = async (action: Action) => {
    try {
      await dispatch(action);
      setError(null);
    } catch (error) {
      setError(String(error));
    }
  };
  const workspace = snapshot?.preferences.workspace ?? defaultWorkspace();
  useEffect(() => {
    if (!snapshot?.preferences.workspace) return;
    setCompact(snapshot.preferences.workspace.compact);
    const screen = snapshot.preferences.workspace.screen;
    setView(screen);
  }, [
    snapshot?.preferences.workspace?.screen,
    snapshot?.preferences.workspace?.compact,
  ]);
  const saveWorkspace = (change: Partial<Workspace>) =>
    void send({
      type: "workspace",
      workspace: normalizeWorkspace({ ...workspace, ...change }),
    });
  const dock =
    workspace.layouts.find((layout) => layout.name === workspace.active_layout)
      ?.root ?? defaultWorkspace().layouts[0].root;
  const saveDock = (root: DockNode) =>
    saveWorkspace({
      layouts: workspace.layouts.map((layout) =>
        layout.name === workspace.active_layout
          ? {
              ...layout,
              root,
              panels: Object.fromEntries(
                panelIds(root).map((id) => [
                  id,
                  layout.panels[id] ?? panelSpec(id as PanelKind),
                ]),
              ),
            }
          : layout,
      ),
    });
  const addPanel = (panel: string) => {
    const id = crypto.randomUUID();
    const append = (node: DockNode): DockNode =>
      node.type === "tabs"
        ? { ...node, panels: [...node.panels, id], active: id }
        : { ...node, first: append(node.first) };
    saveWorkspace({
      layouts: workspace.layouts.map((layout) =>
        layout.name === workspace.active_layout
          ? {
              ...layout,
              root: append(dock),
              panels: { ...layout.panels, [id]: panelSpec(panel as PanelKind) },
            }
          : layout,
      ),
    });
  };
  const updatePanel = (id: string, spec: PanelSpec) =>
    saveWorkspace({
      layouts: workspace.layouts.map((layout) =>
        layout.name === workspace.active_layout
          ? { ...layout, panels: { ...layout.panels, [id]: spec } }
          : layout,
      ),
    });
  const layoutPanels =
    workspace.layouts.find((layout) => layout.name === workspace.active_layout)
      ?.panels ?? defaultWorkspace().layouts[0].panels;
  const applyTemplate = (name: string) => {
    const chartSettings = Object.values(layoutPanels).find(
      (p) => p.kind === "chart",
    )?.chart;
    const group = (...panels: string[]): DockNode => ({
      type: "tabs",
      id: crypto.randomUUID(),
      panels,
      active: panels[0],
    });
    const split = (
      first: DockNode,
      second: DockNode,
      axis: "horizontal" | "vertical" = "horizontal",
      ratio = 0.65,
    ): DockNode => ({
      type: "split",
      id: crypto.randomUUID(),
      axis,
      ratio,
      first,
      second,
    });
    if (name === "Two Chart Desk" || name === "Four Chart Desk") {
      const chartRanges: Range[] =
        name === "Two Chart Desk" ? ["1M", "6M"] : ["1D", "1M", "6M", "1Y"];
      const ids = chartRanges.map(() => crypto.randomUUID()),
        watch = crypto.randomUUID();
      const row = (a: string, b: string) =>
        split(group(a), group(b), "horizontal", 0.5);
      const charts =
        ids.length === 2
          ? row(ids[0], ids[1])
          : split(row(ids[0], ids[1]), row(ids[2], ids[3]), "vertical", 0.5);
      const root = split(group(watch), charts, "horizontal", 0.22);
      const panels: Record<string, PanelSpec> = {
        [watch]: {
          ...panelSpec("watchlist"),
          watchlist_id: snapshot?.preferences.active_watchlist_id ?? "main",
        },
      };
      ids.forEach((id, index) => {
        panels[id] = {
          ...panelSpec("chart"),
          chart: chartSettings,
          drawings: {},
          range: chartRanges[index],
        };
      });
      let unique = name;
      let suffix = 2;
      while (workspace.layouts.some((layout) => layout.name === unique))
        unique = `${name} ${suffix++}`;
      saveWorkspace({
        screen: "dashboard",
        active_layout: unique,
        layouts: [...workspace.layouts, { name: unique, root, panels }],
      });
      return;
    }
    const root =
      name === DEFAULT_LAYOUT_NAME
        ? split(group("watchlist"), group("chart"), "horizontal", 0.19)
        : name === "Portfolio Research"
          ? split(
              split(
                group("portfolio"),
                group("chart", "watchlist"),
                "vertical",
              ),
              group("research"),
            )
          : name === "Screening Desk"
            ? split(
                group("screener"),
                split(group("chart", "watchlist"), group("news"), "vertical"),
              )
            : split(
                split(
                  group("chart", "watchlist"),
                  group("macro", "calendar"),
                  "vertical",
                  0.7,
                ),
                group("news"),
              );
    const ids = new Map(
      panelIds(root).map((kind) => [kind, crypto.randomUUID()]),
    );
    const fresh = (node: DockNode): DockNode =>
      node.type === "tabs"
        ? {
            ...node,
            panels: node.panels.map((p) => ids.get(p)!),
            active: ids.get(node.active)!,
          }
        : { ...node, first: fresh(node.first), second: fresh(node.second) };
    const panels = Object.fromEntries(
      [...ids].map(([kind, id]) => [
        id,
        {
          ...panelSpec(kind as PanelKind),
          ...(kind === "chart" ? { chart: chartSettings, drawings: {} } : {}),
          ...(kind === "watchlist"
            ? {
                watchlist_id:
                  snapshot?.preferences.active_watchlist_id ?? "main",
              }
            : {}),
        },
      ]),
    );
    let unique = name,
      suffix = 2;
    while (workspace.layouts.some((layout) => layout.name === unique))
      unique = `${name} ${suffix++}`;
    saveWorkspace({
      screen: "dashboard",
      active_layout: unique,
      layouts: [
        ...workspace.layouts,
        { name: unique, root: fresh(root), panels },
      ],
    });
  };
  useEffect(() => {
    setError(null);
    let active = true;
    let unlisten: (() => void) | undefined;
    void connect((next) => {
      if (active)
        setSnapshot((previous) =>
          !previous || next.revision >= previous.revision ? next : previous,
        );
    })
      .then((cleanup) => {
        if (active) unlisten = cleanup;
        else cleanup();
      })
      .catch((error) => {
        if (active) setError(String(error));
      });
    const timer = window.setInterval(
      () => setUptime(Math.floor((performance.now() - started.current) / 1000)),
      1000,
    );
    return () => {
      active = false;
      unlisten?.();
      window.clearInterval(timer);
    };
  }, [connectionAttempt]);
  const [sort, setSort] = useState<WatchlistSort>("stored"),
    [descending, setDescending] = useState(false);
  const symbols = sortSymbols(snapshot, sort, descending);
  const priorSymbols = useRef(symbols);
  useLayoutEffect(() => {
    const previous = priorSymbols.current[cursorRef.current];
    const next = previous ? symbols.indexOf(previous) : -1;
    cursorRef.current =
      next >= 0
        ? next
        : Math.min(cursorRef.current, Math.max(symbols.length - 1, 0));
    priorSymbols.current = symbols;
    setCursor(cursorRef.current);
  }, [symbols.join("|")]);
  useLayoutEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (
        event.target instanceof HTMLInputElement ||
        event.target instanceof HTMLSelectElement ||
        event.target instanceof HTMLTextAreaElement ||
        (event.target instanceof HTMLButtonElement && event.key === "Enter")
      )
        return;
      const binding = snapshot?.preferences.keymap.find(({ key }) =>
        typeof key === "string"
          ? key ===
            (
              { ArrowUp: "Up", ArrowDown: "Down", Enter: "Enter" } as Record<
                string,
                string
              >
            )[event.key]
          : "Char" in key
            ? !event.ctrlKey && key.Char === event.key
            : event.ctrlKey && key.Ctrl === event.key.toLowerCase(),
      );
      if (!binding) return;
      event.preventDefault();
      switch (binding.operation) {
        case "Command":
          setCommandOpen(true);
          setCommand(":");
          requestAnimationFrame(() => prompt.current?.focus());
          break;
        case "Up":
          cursorRef.current = Math.max(0, cursorRef.current - 1);
          setCursor(cursorRef.current);
          break;
        case "Down":
          cursorRef.current = Math.max(
            0,
            Math.min(symbols.length - 1, cursorRef.current + 1),
          );
          setCursor(cursorRef.current);
          break;
        case "Select":
          if (symbols[cursorRef.current])
            void send({ type: "select", symbol: symbols[cursorRef.current] });
          break;
        case "NextRange":
          if (snapshot)
            void send({
              type: "set_range",
              range:
                ranges[
                  (ranges.indexOf(snapshot.preferences.range) + 1) %
                    ranges.length
                ],
            });
          break;
        case "Quit":
          void send({ type: "quit" });
          break;
      }
    };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [snapshot, symbols.join("|")]);
  const selected = snapshot?.preferences.selected,
    quote = selected ? snapshot?.quotes[selected]?.data : null;
  const candles = snapshot?.candles.data ?? [],
    change = numeric(quote?.change_percent);
  const current = selected ? snapshot?.quotes[selected] : null;
  const latestBar = candles[candles.length - 1];
  const historyKey = (event: React.KeyboardEvent<HTMLInputElement>) => {
    const history = snapshot?.preferences.history ?? [];
    if (event.key === "Escape") {
      setCommand("");
      setCommandOpen(false);
      historyIndex.current = null;
      prompt.current?.blur();
    }
    if (
      (event.key === "ArrowUp" || event.key === "ArrowDown") &&
      history.length
    ) {
      event.preventDefault();
      if (event.key === "ArrowUp") {
        if (historyIndex.current === null) draft.current = command;
        historyIndex.current =
          historyIndex.current === null
            ? history.length - 1
            : Math.max(historyIndex.current - 1, 0);
      } else if (historyIndex.current !== null)
        historyIndex.current =
          historyIndex.current + 1 < history.length
            ? historyIndex.current + 1
            : null;
      setCommand(
        historyIndex.current === null
          ? draft.current
          : history[historyIndex.current],
      );
    }
  };
  return {
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
  };
}
export type AppState = ReturnType<typeof useAppState>;
function App() {
  const state = useAppState();
  return (
    <StockNames snapshot={state.snapshot}>
      <AppView state={state} />
    </StockNames>
  );
}
const root = document.getElementById("root");
if (root)
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
