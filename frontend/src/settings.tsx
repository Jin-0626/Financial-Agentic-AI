import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { useDesktopView } from "./view-settings";
import { dispatch, type Snapshot } from "./bridge";
export function SettingsScreen({ snapshot }: { snapshot: Snapshot }) {
  const { view, update } = useDesktopView();
  const [keys, setKeys] = useState(
    JSON.stringify(snapshot.preferences.keymap, null, 2),
  );
  const [error, setError] = useState<string | null>(null);
  const [connection, setConnection] = useState<{
    backend_url: string | null;
    organization: string | null;
    user: string | null;
    profile: string;
  } | null>(null);
  useEffect(() => {
    let active = true;
    void invoke<NonNullable<typeof connection>>("terminal_connection")
      .then((value) => {
        if (active) setConnection(value);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);
  return (
    <section className="feature-screen settings-page">
      <nav className="settings-sections" aria-label="Settings sections">
        {[
          ["settings-connection", "Connection"],
          ["settings-keyboard", "Keyboard"],
          ["settings-workspace", "Workspace"],
        ].map(([id, label]) => (
          <button
            key={id}
            onClick={() =>
              document.getElementById(id)?.scrollIntoView({ block: "start" })
            }
          >
            {label}
          </button>
        ))}
      </nav>
      <div className="settings-content">
        <h2 id="settings-connection">Connection</h2>
        <dl className="settings-grid">
          <dt>Provider mode</dt>
          <dd>{snapshot.mode}</dd>
          <dt>Backend</dt>
          <dd>
            {connection?.backend_url ??
              (snapshot.mode === "MOCK"
                ? "Offline mock provider"
                : "Connection information unavailable")}
          </dd>
          <dt>Profile</dt>
          <dd>{connection?.profile ?? "—"}</dd>
          <dt>Organization / user</dt>
          <dd>
            {connection?.organization ?? "Local"} /{" "}
            {connection?.user ?? "Local"}
          </dd>
          <dt>Persistence</dt>
          <dd>
            Native app data / state.sqlite3 · backend and verified identity
            scoped
          </dd>
          <dt>Server records</dt>
          <dd>Portfolios and research remain on the backend</dd>
          <dt>Credentials</dt>
          <dd>Bearer tokens remain in the native process</dd>
        </dl>
        <h3 id="settings-keyboard">Keyboard bindings</h3>
        <p>
          Cover Up, Down, Select, Command, NextRange and Quit with unique keys.
          Character bindings use {JSON.stringify({ Char: "t" })}; control
          bindings use {JSON.stringify({ Ctrl: "c" })}.
        </p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            try {
              const keymap: Snapshot["preferences"]["keymap"] =
                JSON.parse(keys);
              void dispatch({ type: "keymap", keymap })
                .then(() => setError(null))
                .catch((error) => setError(String(error)));
            } catch (error) {
              setError(String(error));
            }
          }}
        >
          <textarea
            aria-label="Keymap JSON"
            value={keys}
            maxLength={16384}
            onChange={(event) => setKeys(event.target.value)}
            rows={15}
          />
          <button>Save keymap</button>
          {error && (
            <p role="alert" className="negative">
              {error}
            </p>
          )}
        </form>
        <h3 id="settings-workspace">Workspace</h3>
        <label>
          <input
            type="checkbox"
            checked={view.rail_collapsed}
            onChange={(e) => update({ rail_collapsed: e.target.checked })}
          />
          Collapsed navigation
        </label>
        <label>
          Ticker
          <select
            aria-label="Settings ticker mode"
            value={view.ticker_mode}
            onChange={(e) =>
              update({ ticker_mode: e.target.value as typeof view.ticker_mode })
            }
          >
            <option value="indices">Market indices</option>
            <option value="watchlist">Active watchlist</option>
          </select>
        </label>
        <label>
          <input
            type="checkbox"
            checked={snapshot.preferences.workspace?.compact ?? false}
            onChange={(event) => {
              const workspace = snapshot.preferences.workspace;
              if (workspace)
                void dispatch({
                  type: "workspace",
                  workspace: { ...workspace, compact: event.target.checked },
                }).catch((error) => setError(String(error)));
            }}
          />
          Compact density
        </label>
        <label>
          Saved layout
          <select
            aria-label="Settings saved layout"
            value={snapshot.preferences.workspace?.active_layout ?? ""}
            onChange={(event) => {
              const workspace = snapshot.preferences.workspace;
              if (workspace)
                void dispatch({
                  type: "workspace",
                  workspace: {
                    ...workspace,
                    active_layout: event.target.value,
                  },
                }).catch((error) => setError(String(error)));
            }}
          >
            {snapshot.preferences.workspace?.layouts.map((layout) => (
              <option key={layout.name}>{layout.name}</option>
            ))}
          </select>
        </label>
        <p>
          Restart the desktop to change backend, profile, or bearer token.
          Credentials stay in the native process.
        </p>
        <p>
          Use the toolbar to change density, save named layouts, and restore
          panels. Ctrl+K opens the command palette. Split dividers support arrow
          keys.
        </p>
      </div>
    </section>
  );
}
