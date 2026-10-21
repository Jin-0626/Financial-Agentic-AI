import type { ChartSettings, Drawing } from "./chart-model";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

export const ranges = [
  "1D",
  "5D",
  "1M",
  "6M",
  "YTD",
  "1Y",
  "5Y",
  "MAX",
] as const;
export type Range = (typeof ranges)[number];
export type Screen =
  | "dashboard"
  | "markets"
  | "watchlist"
  | "crypto"
  | "portfolio"
  | "research"
  | "news"
  | "screener"
  | "options"
  | "macro"
  | "calendar"
  | "backtesting"
  | "traces"
  | "settings";
export type DockNode =
  | { type: "tabs"; id: string; panels: string[]; active: string }
  | {
      type: "split";
      id: string;
      axis: "horizontal" | "vertical";
      ratio: number;
      first: DockNode;
      second: DockNode;
    };
export type Workspace = {
  screen: Screen;
  compact: boolean;
  active_layout: string;
  layouts: NamedLayout[];
  desktop?: DesktopViewSettings;
  browser?: import("./browser-model").BrowserPreferences;
};
export type DesktopViewSettings = {
  rail_collapsed: boolean;
  inspector_open: boolean;
  inspector_width: number;
  inspector_tab: "quote" | "news" | "calendar";
  ticker_mode: "indices" | "watchlist";
  market_tab: "overview" | "MY" | "US" | "crypto";
  portfolio_tab: "holdings" | "transactions" | "performance";
  calendar_tab: "economic" | "earnings";
  calendar_mode: "week" | "table";
};
export const defaultViews = (): DesktopViewSettings => ({
  rail_collapsed: false,
  inspector_open: false,
  inspector_width: 290,
  inspector_tab: "quote",
  ticker_mode: "indices",
  market_tab: "overview",
  portfolio_tab: "holdings",
  calendar_tab: "economic",
  calendar_mode: "week",
});
export type PanelKind =
  | "chart"
  | "watchlist"
  | "news"
  | "macro"
  | "calendar"
  | "screener"
  | "research"
  | "portfolio";
export type PanelSpec = {
  kind: PanelKind;
  binding: { type: "linked" } | { type: "pinned"; instrument: string };
  range: Range | null;
  watchlist_id?: string | null;
  table?: TableSettings;
  chart?: ChartSettings;
  drawings?: Record<string, Drawing[]>;
};
export type QuoteColumn =
  | "instrument"
  | "name"
  | "price"
  | "change"
  | "volume"
  | "currency"
  | "exchange"
  | "status";
export type TableSettings = {
  columns: QuoteColumn[];
  sort: QuoteColumn | null;
  descending: boolean;
};
export type NamedWatchlist = { id: string; name: string; symbols: string[] };
export type WatchlistMutation =
  | { type: "create"; id: string; name: string; duplicate_from: string | null }
  | { type: "rename"; id: string; name: string }
  | { type: "delete" | "activate"; id: string }
  | { type: "order"; ids: string[] }
  | { type: "add" | "reorder"; id: string; symbols: string[] }
  | { type: "remove"; id: string; symbol: string };
export type BulkPreview = {
  symbols: string[];
  duplicates: string[];
  invalid: string[];
  error: string | null;
};
export const defaultTable = (): TableSettings => ({
  columns: ["instrument", "price", "change"],
  sort: null,
  descending: false,
});
export const mutateWatchlist = (mutation: WatchlistMutation) =>
  invoke<void>("terminal_watchlist", { mutation });
export const previewWatchlist = (id: string, text: string) =>
  invoke<BulkPreview>("terminal_watchlist_preview", { id, text });
export const namedLists = (snapshot: Snapshot): NamedWatchlist[] =>
  snapshot.watchlists ?? [
    { id: "main", name: "My Watchlist", symbols: snapshot.watchlist.symbols },
  ];
export type NamedLayout = {
  name: string;
  root: DockNode;
  panels: Record<string, PanelSpec>;
};
export const panelSpec = (kind: PanelKind): PanelSpec => ({
  kind,
  binding: { type: "linked" },
  range: null,
  watchlist_id: kind === "watchlist" ? "main" : null,
  table: defaultTable(),
});
export function panelIds(root: DockNode): string[] {
  return root.type === "tabs"
    ? root.panels
    : [...panelIds(root.first), ...panelIds(root.second)];
}
export function normalizeWorkspace(workspace: Workspace): Workspace {
  return {
    ...workspace,
    layouts: workspace.layouts.map((layout) => ({
      ...layout,
      panels:
        layout.panels ??
        Object.fromEntries(
          panelIds(layout.root).map((id) => [id, panelSpec(id as PanelKind)]),
        ),
    })),
  };
}
export const DEFAULT_LAYOUT_NAME = "Market Desk";
export const defaultWorkspace = (): Workspace => ({
  screen: "dashboard",
  compact: true,
  desktop: defaultViews(),
  active_layout: DEFAULT_LAYOUT_NAME,
  layouts: [
    {
      name: DEFAULT_LAYOUT_NAME,
      panels: { chart: panelSpec("chart"), watchlist: panelSpec("watchlist") },
      root: {
        type: "split",
        id: "root",
        axis: "horizontal",
        ratio: 0.19,
        first: {
          type: "tabs",
          id: "watchlist-group",
          panels: ["watchlist"],
          active: "watchlist",
        },
        second: {
          type: "tabs",
          id: "chart-group",
          panels: ["chart"],
          active: "chart",
        },
      },
    },
  ],
});
export type Instrument = {
  id: string;
  symbol: string;
  name: string;
  asset_class: string;
  exchange: string | null;
};
export type Quote = {
  instrument: Instrument;
  price: string | null;
  change_percent: string | null;
  volume: string | null;
  currency: string | null;
  bid: string | null;
  ask: string | null;
};
export type Candle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
};
export type Market<T> = {
  status: "ok" | "stale" | "unavailable";
  data: T | null;
  source: string | null;
  fetched_at: string | null;
  observed_at: string | null;
  coverage: string | null;
  stale: boolean;
  error: string | null;
  limitations: string[];
  interval: string | null;
};
export type Snapshot = {
  revision: number;
  watchlist: { symbols: string[] };
  watchlists?: NamedWatchlist[];
  preferences: {
    workspace?: Workspace;
    active_watchlist_id?: string;
    selected: string | null;
    range: Range;
    history: string[];
    keymap: {
      key: string | { Char: string } | { Ctrl: string };
      operation: string;
    }[];
  };
  quotes: Record<string, Market<Quote>>;
  candles: Market<Candle[]>;
  mode: "MOCK" | "HTTP";
  message: string;
  latency_ms: number | null;
  quitting: boolean;
};
export type Action =
  | { type: "view"; view: DesktopViewSettings }
  | { type: "select"; symbol: string }
  | { type: "set_range"; range: Range }
  | { type: "command"; text: string }
  | { type: "save" }
  | { type: "workspace"; workspace: Workspace }
  | { type: "keymap"; keymap: Snapshot["preferences"]["keymap"] }
  | { type: "quit" };

export async function connect(onSnapshot: (snapshot: Snapshot) => void) {
  if (!isTauri())
    throw new Error(
      "Open the desktop app with npm run desktop:dev. This interface needs the native Rust engine.",
    );
  let latest: Snapshot | null = null;
  const accept = (snapshot: Snapshot) => {
    if (!latest || snapshot.revision >= latest.revision) {
      if (snapshot.preferences.workspace)
        snapshot = {
          ...snapshot,
          preferences: {
            ...snapshot.preferences,
            workspace: normalizeWorkspace(snapshot.preferences.workspace),
          },
        };
      latest = snapshot;
      onSnapshot(snapshot);
    }
  };
  const unlisten = await listen<
    Omit<Snapshot, "candles"> & { candles?: Snapshot["candles"] }
  >("terminal:snapshot", ({ payload }) => {
    const candles = payload.candles ?? latest?.candles;
    if (candles) accept({ ...payload, candles });
  });
  try {
    accept(await invoke<Snapshot>("terminal_start"));
    // Recover any revision published during initial listener registration/startup.
    accept(await invoke<Snapshot>("terminal_snapshot"));
    return unlisten;
  } catch (error) {
    unlisten();
    throw error;
  }
}
export const dispatch = (action: Action) =>
  invoke<void>("terminal_action", { action });
export function numeric(
  value: string | number | null | undefined,
): number | null {
  if (value == null) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}
export function price(
  value: string | number | null | undefined,
  digits = 2,
): string {
  const number = numeric(value);
  return number === null
    ? "—"
    : number.toLocaleString("en", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
}
export const symbol = (id: string) => id.slice(id.indexOf(":") + 1);
