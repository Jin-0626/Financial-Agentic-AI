import { Dialog } from "./shared-ui";
import { invoke } from "@tauri-apps/api/core";
import { useRef, useState } from "react";
import type { Document, Portfolio } from "./services";
type Preview = {
  preview_hash: string;
  expected_revision: number;
  transaction_count: number;
  document: Document;
};
type Operation =
  | { type: "portfolio_create"; portfolio: Portfolio }
  | {
      type: "portfolio_save";
      id: string;
      revision: number;
      portfolio: Portfolio;
    }
  | { type: "portfolio_delete"; id: string; revision: number }
  | {
      type: "trade";
      id: string;
      revision: number;
      trade: {
        id: string;
        symbol: string;
        side: string;
        quantity: string;
        price: string;
        date: string;
        notes: string;
      };
    }
  | { type: "import_preview"; id: string; mode: "New" | "Merge"; data: string }
  | {
      type: "import_commit";
      id: string;
      mode: "New" | "Merge";
      data: string;
      preview_hash: string;
      expected_revision: number;
    };
const execute = <T,>(request: Operation) =>
  invoke<T>("terminal_service", { request });
export function PortfolioEditor({
  document,
  onSaved,
}: {
  document: Document | null;
  onSaved: (id: string) => void;
}) {
  const [tab, setTab] = useState<
    "new" | "edit" | "trade" | "import" | "delete" | null
  >(null);
  const [name, setName] = useState(""),
    [currency, setCurrency] = useState("USD"),
    [benchmark, setBenchmark] = useState("SPY"),
    [period, setPeriod] = useState("1y");
  const [ticker, setTicker] = useState(""),
    [side, setSide] = useState("BUY"),
    [quantity, setQuantity] = useState(""),
    [price, setPrice] = useState(""),
    [date, setDate] = useState(new Date().toISOString().slice(0, 10)),
    [notes, setNotes] = useState("");
  const [data, setData] = useState(""),
    [mode, setMode] = useState<"New" | "Merge">("New"),
    [preview, setPreview] = useState<Preview | null>(null);
  const [confirmation, setConfirmation] = useState(""),
    [error, setError] = useState<string | null>(null),
    [busy, setBusy] = useState(false);
  const gate = useRef(false),
    tradeId = useRef(crypto.randomUUID());
  function open(value: typeof tab) {
    if (gate.current) return;
    setTab(value);
    setError(null);
    setPreview(null);
    setConfirmation("");
    setName(value === "new" ? "" : (document?.portfolio.name ?? ""));
    setCurrency(document?.portfolio.currency ?? "USD");
    setBenchmark(document?.portfolio.benchmark ?? "SPY");
    setPeriod(document?.portfolio.period ?? "1y");
    tradeId.current = crypto.randomUUID();
  }
  async function submit() {
    if (gate.current) return;
    gate.current = true;
    setBusy(true);
    setError(null);
    try {
      const portfolio: Portfolio = {
        name: name.trim(),
        owner: document?.portfolio.owner ?? "",
        currency,
        benchmark: benchmark.trim(),
        period,
        positions:
          tab === "new"
            ? []
            : (document?.portfolio.positions ?? []).map(
                ({ symbol, quantity, average_cost }) => ({
                  symbol,
                  quantity,
                  average_cost,
                }),
              ),
      };
      let result: Document | null = null;
      switch (tab) {
        case "new":
          result = await execute<Document>({
            type: "portfolio_create",
            portfolio,
          });
          break;
        case "edit":
          if (!document) throw new Error("Choose a portfolio");
          result = await execute<Document>({
            type: "portfolio_save",
            id: document.id,
            revision: document.revision,
            portfolio,
          });
          break;
        case "trade":
          if (!document) throw new Error("Choose a portfolio");
          result = await execute<Document>({
            type: "trade",
            id: document.id,
            revision: document.revision,
            trade: {
              id: tradeId.current,
              symbol: ticker.trim(),
              side,
              quantity,
              price,
              date,
              notes,
            },
          });
          break;
        case "delete":
          if (!document || confirmation !== document.portfolio.name)
            throw new Error("Type the exact portfolio name to delete");
          await execute({
            type: "portfolio_delete",
            id: document.id,
            revision: document.revision,
          });
          setTab(null);
          onSaved("");
          return;
        case "import": {
          const id = document?.id ?? "default";
          if (!preview) {
            setPreview(
              await execute<Preview>({
                type: "import_preview",
                id,
                mode,
                data,
              }),
            );
            return;
          }
          result = await execute<Document>({
            type: "import_commit",
            id,
            mode,
            data,
            preview_hash: preview.preview_hash,
            expected_revision: preview.expected_revision,
          });
          break;
        }
        case null:
          return;
      }
      if (result) {
        onSaved(result.id);
        setTab(null);
        setPreview(null);
      }
    } catch (error) {
      setError(
        `${String(error)}. Refresh the portfolio before retrying a revision conflict.`,
      );
      if (tab === "import") setPreview(null);
    } finally {
      gate.current = false;
      setBusy(false);
    }
  }
  return (
    <section className="portfolio-editor">
      <div className="feature-toolbar">
        <button disabled={busy} onClick={() => open("new")}>
          New portfolio
        </button>
        <button disabled={busy || !document} onClick={() => open("edit")}>
          Edit portfolio
        </button>
        <button disabled={busy || !document} onClick={() => open("trade")}>
          Record transaction
        </button>
        <button disabled={busy} onClick={() => open("import")}>
          Import portfolio
        </button>
        <button disabled={busy || !document} onClick={() => open("delete")}>
          Delete portfolio
        </button>
      </div>
      {tab && (
        <Dialog title="Portfolio editor" busy={busy} onClose={() => open(null)}>
          <form
            aria-label="Portfolio editor"
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
          >
            <fieldset disabled={busy}>
              <legend>
                {tab.toUpperCase()}{" "}
                {document && tab !== "new"
                  ? `· ${document.portfolio.name} · revision ${document.revision}`
                  : ""}
              </legend>
              {(tab === "new" || tab === "edit") && (
                <div className="feature-toolbar">
                  <label>
                    Name
                    <input
                      aria-label="Portfolio name"
                      required
                      maxLength={80}
                      value={name}
                      onChange={(event) => setName(event.target.value)}
                    />
                  </label>
                  <label>
                    Currency
                    <input
                      aria-label="Portfolio currency"
                      required
                      pattern="[A-Z]{3}"
                      maxLength={3}
                      value={currency}
                      disabled={tab === "edit"}
                      onChange={(event) =>
                        setCurrency(event.target.value.toUpperCase())
                      }
                    />
                  </label>
                  <label>
                    Benchmark
                    <input
                      aria-label="Portfolio benchmark"
                      required
                      maxLength={32}
                      value={benchmark}
                      onChange={(event) => setBenchmark(event.target.value)}
                    />
                  </label>
                  <label>
                    Period
                    <select
                      aria-label="Portfolio period"
                      value={period}
                      onChange={(event) => setPeriod(event.target.value)}
                    >
                      {["1mo", "3mo", "6mo", "1y", "2y", "5y"].map((value) => (
                        <option key={value}>{value}</option>
                      ))}
                    </select>
                  </label>
                </div>
              )}
              {tab === "trade" && (
                <div className="feature-toolbar">
                  <label>
                    Symbol
                    <input
                      aria-label="Transaction symbol"
                      required
                      maxLength={32}
                      value={ticker}
                      onChange={(event) => setTicker(event.target.value)}
                    />
                  </label>
                  <label>
                    Side
                    <select
                      aria-label="Transaction side"
                      value={side}
                      onChange={(event) => setSide(event.target.value)}
                    >
                      {["BUY", "SELL", "DIVIDEND"].map((value) => (
                        <option key={value}>{value}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Quantity
                    <input
                      aria-label="Transaction quantity"
                      required
                      inputMode="decimal"
                      value={quantity}
                      onChange={(event) => setQuantity(event.target.value)}
                    />
                  </label>
                  <label>
                    Price
                    <input
                      aria-label="Transaction price"
                      required
                      inputMode="decimal"
                      value={price}
                      onChange={(event) => setPrice(event.target.value)}
                    />
                  </label>
                  <label>
                    Date
                    <input
                      aria-label="Transaction date"
                      required
                      type="date"
                      max={new Date().toISOString().slice(0, 10)}
                      value={date}
                      onChange={(event) => setDate(event.target.value)}
                    />
                  </label>
                  <label>
                    Notes
                    <input
                      aria-label="Transaction notes"
                      maxLength={1000}
                      value={notes}
                      onChange={(event) => setNotes(event.target.value)}
                    />
                  </label>
                </div>
              )}
              {tab === "import" && (
                <>
                  <label>
                    Mode
                    <select
                      aria-label="Import mode"
                      value={mode}
                      onChange={(event) => {
                        setMode(event.target.value as "New" | "Merge");
                        setPreview(null);
                      }}
                    >
                      <option>New</option>
                      <option disabled={!document}>Merge</option>
                    </select>
                  </label>
                  <textarea
                    aria-label="Import portfolio JSON"
                    required
                    maxLength={1048576}
                    rows={8}
                    value={data}
                    onChange={(event) => {
                      setData(event.target.value);
                      setPreview(null);
                    }}
                  />
                  {preview && (
                    <section aria-label="Import preview">
                      <h3>{preview.document.portfolio.name}</h3>
                      <p>
                        {preview.transaction_count} transactions ·{" "}
                        {preview.document.portfolio.currency} · expected
                        revision {preview.expected_revision}
                      </p>
                      <pre>
                        {JSON.stringify(
                          preview.document.portfolio.positions,
                          null,
                          2,
                        )}
                      </pre>
                    </section>
                  )}
                </>
              )}
              {tab === "delete" && (
                <label>
                  Type {document?.portfolio.name} to delete its saved SQLite
                  record
                  <input
                    aria-label="Confirm portfolio deletion"
                    required
                    value={confirmation}
                    onChange={(event) => setConfirmation(event.target.value)}
                  />
                </label>
              )}
              <button
                type="submit"
                disabled={
                  tab === "delete" && confirmation !== document?.portfolio.name
                }
              >
                {busy
                  ? "Saving…"
                  : tab === "import"
                    ? preview
                      ? "Commit reviewed import"
                      : "Preview import"
                    : tab === "delete"
                      ? "Delete confirmed portfolio"
                      : "Save changes"}
              </button>
              <button type="button" onClick={() => open(null)}>
                Cancel
              </button>
            </fieldset>
            {error && (
              <p className="negative" role="alert">
                {error}
              </p>
            )}
          </form>
        </Dialog>
      )}
    </section>
  );
}
