import {
  createContext,
  useContext,
  useEffect,
  useState,
  ReactNode,
} from "react";
import type { Identity } from "../api";
export type Instrument = {
  id: string;
  symbol: string;
  name: string;
  asset_class: string;
  exchange?: string | null;
};
export const widgetTypes = [
  "chart",
  "quote",
  "watchlist",
  "news",
  "screener",
  "heatmap",
  "options",
  "crypto",
  "macro",
  "portfolio",
  "calendar",
] as const;
export type WidgetType = (typeof widgetTypes)[number];
export type Widget = {
  id: string;
  type: WidgetType;
  linked: boolean;
  instrument?: Instrument;
  settings?: Record<string, string>;
};
export type Cell = { i: string; x: number; y: number; w: number; h: number };
export type Workspace = {
  version: 1;
  active: Instrument;
  widgets: Widget[];
  layout: Cell[];
  watchlist: Instrument[];
  researchOpen: boolean;
};
const initial: Instrument = {
  id: "yahoo:5326.KL",
  symbol: "5326.KL",
  name: "5326.KL",
  asset_class: "equity",
  exchange: "Bursa",
};
export function defaults(): Workspace {
  return {
    version: 1,
    active: initial,
    researchOpen: true,
    watchlist: [initial],
    widgets: ["chart", "quote", "watchlist", "news", "macro"].map(
      (type, i) => ({
        id: `default-${i}`,
        type: type as WidgetType,
        linked: true,
      }),
    ),
    layout: [
      { i: "default-0", x: 0, y: 0, w: 8, h: 12 },
      { i: "default-1", x: 8, y: 0, w: 4, h: 8 },
      { i: "default-2", x: 8, y: 8, w: 4, h: 7 },
      { i: "default-3", x: 0, y: 12, w: 8, h: 9 },
      { i: "default-4", x: 8, y: 15, w: 4, h: 9 },
    ],
  };
}
function validInstrument(value: unknown): value is Instrument {
  if (!value || typeof value !== "object") return false;
  const item = value as Instrument;
  return (
    typeof item.id === "string" &&
    /^(yahoo:[A-Za-z0-9.^=_-]{1,32}|coingecko:[a-z0-9-]{1,100})$/.test(
      item.id,
    ) &&
    typeof item.symbol === "string" &&
    typeof item.name === "string" &&
    item.name.length <= 200 &&
    typeof item.asset_class === "string"
  );
}
export function restore(raw: string | null): Workspace {
  if (!raw || raw.length > 65536) return defaults();
  try {
    const value = JSON.parse(raw) as Workspace;
    if (
      value.version !== 1 ||
      !validInstrument(value.active) ||
      !Array.isArray(value.widgets) ||
      value.widgets.length > 20 ||
      !Array.isArray(value.layout) ||
      !Array.isArray(value.watchlist) ||
      value.watchlist.length > 100
    )
      return defaults();
    const ids = new Set<string>();
    for (const widget of value.widgets) {
      if (
        !widget ||
        typeof widget.id !== "string" ||
        ids.has(widget.id) ||
        !widgetTypes.includes(widget.type) ||
        typeof widget.linked !== "boolean" ||
        (widget.instrument && !validInstrument(widget.instrument))
      )
        return defaults();
      ids.add(widget.id);
    }
    if (
      value.layout.length !== ids.size ||
      new Set(value.layout.map((c) => c.i)).size !== ids.size ||
      value.layout.some(
        (c) =>
          !ids.has(c.i) ||
          ![c.x, c.y, c.w, c.h].every(Number.isInteger) ||
          c.x < 0 ||
          c.y < 0 ||
          c.y > 1000 ||
          c.w < 2 ||
          c.w > 12 ||
          c.x + c.w > 12 ||
          c.h < 3 ||
          c.h > 40,
      ) ||
      value.watchlist.some((i) => !validInstrument(i)) ||
      new Set(value.watchlist.map((i) => i.id)).size !== value.watchlist.length
    )
      return defaults();
    return { ...value, researchOpen: value.researchOpen !== false };
  } catch {
    return defaults();
  }
}
type Controls = {
  state: Workspace;
  update: (fn: (value: Workspace) => Workspace) => void;
  add: (type: WidgetType) => void;
  select: (instrument: Instrument) => void;
  watch: (instrument: Instrument) => void;
  reset: () => void;
  storageError: string;
};
const Context = createContext<Controls | null>(null);
export function TerminalProvider({
  identity,
  children,
}: {
  identity: Identity;
  children: ReactNode;
}) {
  const key = `financial-terminal:v1:${JSON.stringify([identity.org, identity.user])}`;
  const [state, setState] = useState<Workspace>(() => {
    try {
      return restore(localStorage.getItem(key));
    } catch {
      return defaults();
    }
  });
  const [storageError, setStorageError] = useState("");
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(state));
      setStorageError("");
    } catch {
      setStorageError(
        "Browser storage unavailable; layout will not survive reload.",
      );
    }
  }, [key, state]);
  function add(type: WidgetType) {
    setState((old) => {
      if (
        old.widgets.length >= 20 ||
        (type === "portfolio" && old.widgets.some((w) => w.type === type))
      )
        return old;
      const id = crypto.randomUUID();
      const y = Math.max(0, ...old.layout.map((c) => c.y + c.h));
      return {
        ...old,
        widgets: [...old.widgets, { id, type, linked: true }],
        layout: [
          ...old.layout,
          {
            i: id,
            x: 0,
            y,
            w:
              type === "portfolio" || type === "screener" || type === "options"
                ? 12
                : 6,
            h: type === "portfolio" ? 20 : 10,
          },
        ],
      };
    });
  }
  return (
    <Context.Provider
      value={{
        state,
        update: setState,
        add,
        select: (active) => setState((old) => ({ ...old, active })),
        watch: (instrument) =>
          setState((old) =>
            old.watchlist.some((i) => i.id === instrument.id) ||
            old.watchlist.length >= 100
              ? old
              : { ...old, watchlist: [...old.watchlist, instrument] },
          ),
        reset: () => setState(defaults()),
        storageError,
      }}
    >
      {children}
    </Context.Provider>
  );
}
export function useTerminal() {
  const value = useContext(Context);
  if (!value) throw new Error("Terminal provider missing");
  return value;
}
