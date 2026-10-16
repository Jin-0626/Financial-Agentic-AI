import { useEffect, useLayoutEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { SymbolSearch } from "./symbol-search";
import type { Screen } from "./bridge";
import { screenLabels } from "./navigation";
export function TitleBarTools({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  useLayoutEffect(
    () => setTarget(document.getElementById("desktop-context-tools")),
    [],
  );
  return target ? createPortal(children, target) : null;
}
export function TitleBar({
  screen,
  onNavigate,
  onSelect,
  enabled,
}: {
  screen: Screen;
  onNavigate: (screen: Screen) => void;
  onSelect: (id: string) => void;
  enabled: boolean;
}) {
  const [history, setHistory] = useState({ entries: [screen], index: 0 }),
    [maximized, setMaximized] = useState(false),
    [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setHistory((previous) =>
      previous.entries[previous.index] === screen
        ? previous
        : {
            entries: [
              ...previous.entries.slice(0, previous.index + 1),
              screen,
            ].slice(-40),
            index: Math.min(previous.index + 1, 39),
          },
    );
  }, [screen]);
  useEffect(() => {
    if (!isTauri()) return;
    const refresh = () => {
      void getCurrentWindow()
        .isMaximized()
        .then(setMaximized)
        .catch(() => {});
    };
    refresh();
    window.addEventListener("resize", refresh);
    return () => window.removeEventListener("resize", refresh);
  }, []);
  const control = async (action: "minimize" | "maximize" | "close") => {
    if (!isTauri()) return;
    try {
      const win = getCurrentWindow();
      switch (action) {
        case "minimize":
          await win.minimize();
          break;
        case "maximize":
          await win.toggleMaximize();
          setMaximized(await win.isMaximized());
          break;
        case "close":
          await win.close();
          break;
      }
    } catch (e) {
      setError(String(e));
    }
  };
  const navigate = (index: number) => {
    setHistory((previous) => ({ ...previous, index }));
    onNavigate(history.entries[index]);
  };
  return (
    <header
      className="title-bar"
      data-tauri-drag-region
      onDoubleClick={(event) => {
        if (
          event.target instanceof HTMLElement &&
          event.target.hasAttribute("data-tauri-drag-region")
        )
          void control("maximize");
      }}
    >
      <div className="title-history">
        <button
          aria-label="Back"
          disabled={!history.index}
          onClick={() => navigate(history.index - 1)}
        >
          ←
        </button>
        <button
          aria-label="Forward"
          disabled={history.index === history.entries.length - 1}
          onClick={() => navigate(history.index + 1)}
        >
          →
        </button>
      </div>
      <SymbolSearch enabled={enabled} onSelect={onSelect} />
      <h1 className="visually-hidden">{screenLabels[screen]}</h1>
      <div className="app-title" data-tauri-drag-region>
        Financial Research Terminal
      </div>
      {error && (
        <span role="alert" className="window-error" title={error}>
          Window control unavailable
        </span>
      )}
      <div id="desktop-context-tools" aria-label="Desktop actions" />
      <div className="window-controls">
        <button
          aria-label="Minimize window"
          onClick={() => void control("minimize")}
        >
          ─
        </button>
        <button
          aria-label={maximized ? "Restore window" : "Maximize window"}
          onClick={() => void control("maximize")}
        >
          {maximized ? "❐" : "□"}
        </button>
        <button
          className="window-close"
          aria-label="Close window"
          onClick={() => void control("close")}
        >
          ×
        </button>
      </div>
    </header>
  );
}
