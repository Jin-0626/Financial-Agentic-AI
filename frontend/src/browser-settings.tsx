import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  defaultBrowser,
  marketKey,
  type BrowserPreferences,
  type BrowserMutation,
  type PresetAction,
  type Universe,
} from "./browser-model";
import { Dialog } from "./shared-ui";
const initial = defaultBrowser();
const Context = createContext({
  preferences: initial,
  pending: false,
  error: null as string | null,
  mutate: (_mutation: BrowserMutation) => {},
  retry: () => {},
  discard: () => {},
});
export function BrowserSettingsProvider({
  saved,
  children,
}: {
  saved?: BrowserPreferences;
  children: ReactNode;
}) {
  const source = JSON.stringify(saved ?? initial);
  const [preferences, setPreferences] = useState(saved ?? initial),
    [pending, setPending] = useState(false),
    [error, setError] = useState<string | null>(null);
  const last = useRef<BrowserMutation | null>(null),
    busy = useRef(false);
  useEffect(() => {
    if (!last.current) setPreferences(JSON.parse(source));
  }, [source]);
  const perform = async (mutation: BrowserMutation) => {
    if (busy.current) return;
    busy.current = true;
    setPending(true);
    setError(null);
    last.current = mutation;
    if (mutation.type === "market")
      setPreferences((previous) => ({
        ...previous,
        [marketKey(mutation.market)]: mutation.settings,
      }));
    if (mutation.type === "options")
      setPreferences((previous) => ({
        ...previous,
        options: mutation.settings,
      }));
    try {
      const result = await invoke<BrowserPreferences>("terminal_browser", {
        mutation,
      });
      last.current = null;
      if (result) setPreferences(result);
    } catch (e) {
      setError(String(e));
    } finally {
      busy.current = false;
      setPending(false);
    }
  };
  // Successful native snapshots are authoritative; a failed draft is retained.

  return (
    <Context.Provider
      value={{
        preferences,
        pending,
        error,
        mutate: (mutation) => void perform(mutation),
        retry: () => {
          if (last.current) void perform(last.current);
        },
        discard: () => {
          last.current = null;
          setError(null);
          setPreferences(JSON.parse(source));
        },
      }}
    >
      {children}
    </Context.Provider>
  );
}
export const useBrowserSettings = () => useContext(Context);
export function BrowserSaveStatus() {
  const { pending, error, retry, discard } = useBrowserSettings();
  return (
    <>
      {pending && <p role="status">Saving display settings…</p>}
      {error && (
        <div className="browser-save-error" role="alert">
          Unsaved display draft: {error}
          <button onClick={retry}>Retry display save</button>
          <button onClick={discard}>Discard display draft</button>
        </div>
      )}
    </>
  );
}
export function PresetControls({ market }: { market?: Universe }) {
  const { preferences, pending, error, mutate } = useBrowserSettings(),
    presets = market ? preferences.market_presets : preferences.options_presets;
  const [selected, setSelected] = useState(""),
    [name, setName] = useState(""),
    [confirm, setConfirm] = useState(false);
  const act = (action: PresetAction) =>
    mutate(
      market
        ? { type: "market_preset", market, action }
        : { type: "options_preset", action },
    );
  const exists = presets.some((p) => p.name === selected),
    disabled = pending || !!error,
    newName = name.trim(),
    valid =
      !!newName &&
      newName.length <= 64 &&
      !presets.some((p) => p.name === newName);
  return (
    <details className="browser-presets">
      <summary>Presets</summary>
      <div className="preset-controls">
        <label>
          Saved preset
          <select
            aria-label={market ? "Market preset" : "Options preset"}
            value={exists ? selected : ""}
            onChange={(e) => setSelected(e.target.value)}
          >
            <option value="">Choose preset</option>
            {presets.map((p) => (
              <option key={p.name}>{p.name}</option>
            ))}
          </select>
        </label>
        <label>
          Name
          <input
            aria-label="Preset name"
            maxLength={64}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <button
          disabled={disabled || !valid || presets.length >= 20}
          onClick={() =>
            act({ type: "save", name: newName, duplicate_from: null })
          }
        >
          Save new preset
        </button>
        <button
          disabled={disabled || !valid || !exists || presets.length >= 20}
          onClick={() =>
            act({ type: "save", name: newName, duplicate_from: selected })
          }
        >
          Duplicate preset
        </button>
        <button
          disabled={disabled || !valid || !exists}
          onClick={() =>
            act({ type: "rename", name: selected, new_name: newName })
          }
        >
          Rename preset
        </button>
        <button
          disabled={disabled || !exists}
          onClick={() => act({ type: "apply", name: selected })}
        >
          Apply preset
        </button>
        <button disabled={disabled || !exists} onClick={() => setConfirm(true)}>
          Delete preset
        </button>
        <button disabled={disabled} onClick={() => act({ type: "reset" })}>
          Reset display defaults
        </button>
        <small>
          {presets.length}/20 local presets. Expiry and selected contracts are
          not stored.
        </small>
      </div>
      {confirm && (
        <Dialog title="Delete display preset" onClose={() => setConfirm(false)}>
          <p>Delete “{selected}”? Current display settings stay unchanged.</p>
          <button
            onClick={() => {
              act({ type: "delete", name: selected });
              setConfirm(false);
            }}
          >
            Confirm preset deletion
          </button>
          <button onClick={() => setConfirm(false)}>Cancel</button>
        </Dialog>
      )}
    </details>
  );
}
export function ColumnControls<T extends string>({
  title,
  columns,
  labels,
  required,
  onSave,
  disabled,
}: {
  title: string;
  columns: T[];
  labels: Record<T, string>;
  required?: T;
  onSave: (columns: T[]) => void;
  disabled: boolean;
}) {
  const [open, setOpen] = useState(false),
    [draft, setDraft] = useState(columns);
  return (
    <>
      <button
        disabled={disabled}
        onClick={() => {
          setDraft([...columns]);
          setOpen(true);
        }}
      >
        {title}
      </button>
      {open && (
        <Dialog title={title} onClose={() => setOpen(false)}>
          <p>
            Show, hide and order columns.{" "}
            {required
              ? "Instrument always remains visible."
              : "Strike always remains visible; keep at least one quote field."}
          </p>
          {(Object.keys(labels) as T[]).map((column) => (
            <label className="column-toggle" key={column}>
              <input
                type="checkbox"
                checked={draft.includes(column)}
                disabled={column === required}
                onChange={(e) =>
                  setDraft(
                    e.target.checked
                      ? [...draft, column]
                      : draft.filter((c) => c !== column),
                  )
                }
              />
              {labels[column]}
            </label>
          ))}
          <ol className="column-order">
            {draft.map((column, index) => (
              <li key={column}>
                {labels[column]}
                <button
                  disabled={!index}
                  onClick={() => {
                    const next = [...draft];
                    [next[index - 1], next[index]] = [
                      next[index],
                      next[index - 1],
                    ];
                    setDraft(next);
                  }}
                  aria-label={`Move ${labels[column]} column up`}
                >
                  ↑
                </button>
                <button
                  disabled={index === draft.length - 1}
                  onClick={() => {
                    const next = [...draft];
                    [next[index + 1], next[index]] = [
                      next[index],
                      next[index + 1],
                    ];
                    setDraft(next);
                  }}
                  aria-label={`Move ${labels[column]} column down`}
                >
                  ↓
                </button>
              </li>
            ))}
          </ol>
          <button
            disabled={!draft.length}
            onClick={() => {
              onSave(draft);
              setOpen(false);
            }}
          >
            Apply columns
          </button>
          <button onClick={() => setDraft(columns)}>Undo column changes</button>
          <button
            onClick={() => {
              const defaults = required
                ? [
                    "instrument",
                    "price",
                    "change_percent",
                    "market_cap",
                    "volume",
                    "sector",
                  ]
                : [
                    "bid",
                    "ask",
                    "last",
                    "implied_volatility",
                    "open_interest",
                    "volume",
                  ];
              setDraft(defaults as T[]);
            }}
          >
            Reset columns
          </button>
        </Dialog>
      )}
    </>
  );
}
