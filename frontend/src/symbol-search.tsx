import { useState } from "react";
import { useResource } from "./resources";
export function SymbolSearch({
  enabled,
  onSelect,
}: {
  enabled: boolean;
  onSelect: (id: string) => void;
}) {
  const [input, setInput] = useState(""),
    [query, setQuery] = useState<string | null>(null);
  const result = useResource(query ? { type: "search", query } : null, enabled);
  const rows =
    result.payload?.type === "search" ? result.payload.market.data : null;
  return (
    <div className="symbol-search">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (input.trim()) setQuery(input.trim());
        }}
      >
        <input
          aria-label="Global instrument search"
          placeholder="Search symbol or company"
          value={input}
          maxLength={80}
          disabled={!enabled}
          onChange={(event) => setInput(event.target.value)}
        />
        <button disabled={!enabled || !input.trim()}>Search</button>
      </form>
      {query && (
        <section
          className="search-results"
          aria-label="Instrument search results"
        >
          <button className="search-close" onClick={() => setQuery(null)}>
            Close
          </button>
          {result.error && <p role="alert">{result.error}</p>}
          {!result.payload && !result.error && <p>Searching providers…</p>}
          {rows?.map((row) => (
            <button
              key={row.id}
              onClick={() => {
                onSelect(row.id);
                setQuery(null);
                setInput("");
              }}
            >
              <strong>{row.symbol}</strong>
              <span>
                {row.name} · {row.exchange ?? row.asset_class}
              </span>
            </button>
          ))}
          {rows && !rows.length && <p>No verified instruments found.</p>}
        </section>
      )}
    </div>
  );
}
