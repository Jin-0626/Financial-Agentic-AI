import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { Instrument, useTerminal } from "./state";
import { Market } from "./market";
export function InstrumentPalette() {
  const dialog = useRef<HTMLDialogElement>(null),
    input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState(""),
    [results, setResults] = useState<Instrument[]>([]),
    [selected, setSelected] = useState(0),
    [error, setError] = useState(""),
    [opened, setOpened] = useState(false);
  const terminal = useTerminal(),
    navigate = useNavigate();
  const commands = [
    { name: "Terminal", path: "/terminal" },
    { name: "Portfolio", path: "/terminal?widget=portfolio&focus=portfolio" },
    { name: "Research", path: "/terminal?research=1&focus=research" },
    { name: "Traces", path: "/traces" },
    { name: "Backtesting", path: "/backtesting" },
    { name: "Terminal modules", path: "/modules" },
  ].filter((c) =>
    c.name.toLowerCase().includes(query.slice(1).trim().toLowerCase()),
  );
  function open() {
    setQuery("");
    setResults([]);
    setSelected(0);
    setOpened(true);
    dialog.current?.showModal();
    input.current?.focus();
  }
  function close() {
    dialog.current?.close();
    setOpened(false);
  }
  function pick(index: number, watch = false) {
    if (query.startsWith(">")) {
      if (commands[index]) {
        navigate(commands[index].path);
        close();
      }
    } else if (results[index]) {
      terminal.select(results[index]);
      if (watch) terminal.watch(results[index]);
      navigate("/terminal");
      close();
    }
  }
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (dialog.current?.open) close();
        else open();
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  useEffect(() => {
    setSelected(0);
    setError("");
    if (!opened || query.startsWith(">") || !query.trim()) {
      setResults([]);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(
      () =>
        void api<Market<Instrument[]>>(
          `/market/search?query=${encodeURIComponent(query.trim())}`,
          { signal: controller.signal },
        )
          .then((value) => {
            if (!controller.signal.aborted) {
              setResults(value.data || []);
              setError(
                value.status === "unavailable"
                  ? value.error || "Search unavailable"
                  : "",
              );
            }
          })
          .catch((e) => {
            if (!controller.signal.aborted) setError(e.message);
          }),
      250,
    );
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, opened]);
  const count = query.startsWith(">") ? commands.length : results.length;
  return (
    <>
      <button className="workspace-jump" onClick={open}>
        Search symbol / workspace <kbd>Ctrl K</kbd>
      </button>
      <dialog
        ref={dialog}
        onClose={() => setOpened(false)}
        className="workspace-dialog"
        aria-labelledby="instrument-search-title"
      >
        <div className="panel-head">
          <h2 id="instrument-search-title">Search instruments</h2>
          <button onClick={close} aria-label="Close symbol search">
            ×
          </button>
        </div>
        <input
          ref={input}
          aria-label="Search instruments or commands"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setResults([]);
          }}
          placeholder="Ticker, company, ETF, crypto or > command"
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setSelected((n) => Math.min(n + 1, Math.max(0, count - 1)));
            }
            if (e.key === "ArrowUp") {
              e.preventDefault();
              setSelected((n) => Math.max(0, n - 1));
            }
            if (e.key === "Enter") {
              e.preventDefault();
              pick(selected, e.shiftKey);
            }
          }}
        />
        {error && <p role="alert">{error}</p>}
        <div className="workspace-options">
          {query.startsWith(">")
            ? commands.map((c, i) => (
                <button
                  key={c.path}
                  className={i === selected ? "selected" : ""}
                  onClick={() => pick(i)}
                >
                  {c.name}
                </button>
              ))
            : results.map((r, i) => (
                <button
                  key={r.id}
                  className={i === selected ? "selected" : ""}
                  onClick={() => pick(i)}
                >
                  <b>
                    {r.symbol} · {r.name}
                  </b>
                  <small>
                    {r.exchange} · {r.asset_class}
                  </small>
                </button>
              ))}
        </div>
        {query.trim() && !count && !error && <p>No matching results.</p>}
        <p className="switcher-hint">
          ↑↓ select · Enter open · Shift+Enter add to watchlist · &gt; commands
          · Esc close
        </p>
      </dialog>
    </>
  );
}
