import { desktop, openPortfolioFile } from "../../shared/desktop";
import { api, Identity, scope, send } from "../../shared/api";
import { Document, Preview } from "./types";
import { usePortfolioWorkspace } from "./use-workspace";
import { usePortfolioEditor } from "./use-editor";
type Props = {
  identity: Identity;
  workspace: ReturnType<typeof usePortfolioWorkspace>;
  editor: ReturnType<typeof usePortfolioEditor>;
};
export function PortfolioDialog({ identity, workspace, editor }: Props) {
  const {
    list,
    id,
    setId,
    doc,
    setDoc,
    summary,
    error,
    setError,
    busy,
    reloadList,
    refresh,
    act,
  } = workspace;
  const {
    dialog,
    setDialog,
    editingTrade,
    name,
    setName,
    currency,
    setCurrency,
    benchmark,
    setBenchmark,
    query,
    setQuery,
    symbols,
    setSymbols,
    symbol,
    setSymbol,
    side,
    setSide,
    quantity,
    setQuantity,
    price,
    setPrice,
    date,
    setDate,
    notes,
    setNotes,
    importData,
    setImportData,
    mode,
    setMode,
    preview,
    setPreview,
    tradeId,
    open,
    selectCorrection,
    editHolding,
  } = editor;
  const currencyCode = doc?.portfolio.currency || "USD";
  return (
    <>
      {dialog && (
        <div className="modal-backdrop">
          <section
            role="dialog"
            aria-modal="true"
            aria-label={dialog}
            className="modal"
          >
            <div className="panel-head">
              <h2>{dialog}</h2>
              <button aria-label="Close dialog" onClick={() => setDialog("")}>
                ×
              </button>
            </div>
            {error && (
              <p role="alert" className="error">
                {error}
              </p>
            )}
            {(dialog === "New portfolio" || dialog === "Settings") && (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void act(async () => {
                    const p = {
                      name,
                      currency,
                      benchmark,
                      period: doc?.portfolio.period || "1y",
                      owner:
                        dialog === "Settings" ? doc?.portfolio.owner || "" : "",
                      positions: [],
                    };
                    const saved = await api<Document>(
                      dialog === "New portfolio"
                        ? `/portfolio/create?${scope(identity)}`
                        : `/portfolio?${scope(identity, id, doc?.revision)}`,
                      send(p, dialog === "New portfolio" ? "POST" : "PUT"),
                    );
                    setDialog("");
                    await reloadList(saved.id);
                    if (saved.id === id) await refresh();
                  });
                }}
              >
                <label>
                  Name
                  <input
                    required
                    maxLength={80}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </label>
                <label>
                  Currency
                  <input
                    required
                    pattern="[A-Z]{3}"
                    disabled={dialog === "Settings"}
                    value={currency}
                    onChange={(e) => setCurrency(e.target.value.toUpperCase())}
                  />
                </label>
                <label>
                  Benchmark (exact Yahoo symbol)
                  <input
                    required
                    value={benchmark}
                    onChange={(e) => setBenchmark(e.target.value.toUpperCase())}
                  />
                </label>
                <button disabled={busy} className="accent">
                  Save portfolio
                </button>
                {dialog === "Settings" && (
                  <button
                    type="button"
                    className="danger"
                    disabled={busy}
                    onClick={() => open("Delete portfolio")}
                  >
                    Delete portfolio…
                  </button>
                )}
              </form>
            )}
            {dialog === "Delete portfolio" && (
              <>
                <p>
                  Delete {doc?.portfolio.name} and its transaction records from
                  this workspace?
                </p>
                <button
                  className="danger"
                  onClick={() =>
                    void act(async () => {
                      await api(
                        `/portfolio?${scope(identity, id, doc?.revision)}`,
                        { method: "DELETE" },
                      );
                      setDialog("");
                      await reloadList();
                    })
                  }
                >
                  Delete permanently
                </button>
              </>
            )}
            {(dialog === "Record trade" || dialog === "Edit holding") && (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void act(async () => {
                    const correcting = dialog === "Edit holding";
                    if (correcting && !editingTrade)
                      throw new Error("Choose a transaction to correct");
                    const saved = await api<Document>(
                      `/portfolio/transactions${correcting ? `/${encodeURIComponent(editingTrade!.id)}` : ""}?${scope(identity, id, doc?.revision)}`,
                      send(
                        correcting
                          ? { quantity, price, date, notes }
                          : {
                              id: tradeId.current,
                              symbol,
                              side,
                              quantity,
                              price,
                              date,
                              notes,
                            },
                        correcting ? "PUT" : "POST",
                      ),
                    );
                    setDoc(saved);
                    setDialog("");
                    await refresh();
                  });
                }}
              >
                {dialog === "Edit holding" && (
                  <>
                    <p className="notice">
                      Correct a recorded transaction for {symbol}. Holdings and
                      gains are recalculated. Original values remain in the
                      audit history. Earlier valuations are excluded from
                      corrected performance.
                    </p>
                    <label>
                      Transaction to correct
                      <select
                        aria-label="Transaction to correct"
                        value={editingTrade?.id || ""}
                        onChange={(e) => {
                          const t = doc?.transactions.find(
                            (t) => t.id === e.target.value,
                          );
                          if (t) selectCorrection(t);
                        }}
                      >
                        {doc?.transactions
                          .filter((t) => t.symbol === symbol)
                          .map((t) => (
                            <option key={t.id} value={t.id}>
                              {t.date} · {t.side} · {t.quantity} @ {t.price}
                            </option>
                          ))}
                      </select>
                    </label>
                  </>
                )}
                {dialog === "Record trade" && (
                  <>
                    <label>
                      Find company or symbol
                      <div className="inline">
                        <input
                          value={query}
                          onChange={(e) => setQuery(e.target.value)}
                          placeholder="e.g. Apple or 1155.KL"
                        />
                        <button
                          type="button"
                          onClick={() =>
                            void act(async () => {
                              const data = await api<{
                                symbols: typeof symbols;
                              }>(
                                `/portfolio/symbols?query=${encodeURIComponent(query)}`,
                              );
                              setSymbols(data.symbols);
                            })
                          }
                        >
                          Search
                        </button>
                      </div>
                    </label>
                    <label>
                      Exact symbol
                      <select
                        required
                        aria-label="Trade symbol"
                        value={symbol}
                        onChange={(e) => setSymbol(e.target.value)}
                      >
                        <option value="">Choose a search result</option>
                        {symbols.map((s) => (
                          <option key={s.symbol} value={s.symbol}>
                            {s.symbol} · {s.name} · {s.exchange}
                          </option>
                        ))}
                      </select>
                    </label>
                  </>
                )}
                <div className="form-row">
                  <label>
                    Type
                    <select
                      disabled={dialog === "Edit holding"}
                      value={side}
                      onChange={(e) => setSide(e.target.value)}
                    >
                      {["BUY", "SELL", "DIVIDEND"].map((s) => (
                        <option key={s}>{s}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Date
                    <input
                      type="date"
                      required
                      max={new Date().toISOString().slice(0, 10)}
                      value={date}
                      onChange={(e) => setDate(e.target.value)}
                    />
                  </label>
                </div>
                <div className="form-row">
                  <label>
                    Quantity
                    <input
                      required
                      type="number"
                      step="any"
                      min="0.00000001"
                      value={quantity}
                      onChange={(e) => setQuantity(e.target.value)}
                    />
                  </label>
                  <label>
                    {side === "DIVIDEND" ? "Dividend per share" : "Price"} (
                    {currencyCode})
                    <input
                      required
                      type="number"
                      step="any"
                      min="0"
                      value={price}
                      onChange={(e) => setPrice(e.target.value)}
                    />
                  </label>
                </div>
                <label>
                  Notes
                  <input
                    maxLength={1000}
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                  />
                </label>
                <button disabled={busy} className="accent">
                  {dialog === "Edit holding"
                    ? "Save correction"
                    : "Record transaction"}
                </button>
              </form>
            )}
            {dialog === "Import JSON" && desktop && (
              <button
                onClick={() =>
                  void act(async () => {
                    const text = await openPortfolioFile();
                    if (text !== null) {
                      setImportData(text);
                      setPreview(null);
                    }
                  })
                }
              >
                Choose portfolio JSON
              </button>
            )}
            {dialog === "Import JSON" && (
              <>
                <label>
                  Import mode
                  <select
                    value={mode}
                    onChange={(e) => {
                      setMode(e.target.value);
                      setPreview(null);
                    }}
                  >
                    <option>New</option>
                    <option disabled={!id}>Merge</option>
                  </select>
                </label>
                <label>
                  Financial transaction file
                  <input
                    type="file"
                    accept=".json,application/json"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file && file.size <= 1048576)
                        void file.text().then((text) => {
                          setImportData(text);
                          setPreview(null);
                        });
                      else setError("Choose a JSON file no larger than 1 MiB");
                    }}
                  />
                </label>
                <label>
                  JSON
                  <textarea
                    rows={8}
                    value={importData}
                    onChange={(e) => {
                      setImportData(e.target.value);
                      setPreview(null);
                    }}
                  />
                </label>
                <button
                  disabled={busy}
                  onClick={() =>
                    void act(async () => {
                      setPreview(
                        await api<Preview>(
                          `/portfolio/import/preview?${scope(identity, id || "default")}`,
                          send({ mode, data: JSON.parse(importData) }),
                        ),
                      );
                    })
                  }
                >
                  Preview import
                </button>
                {preview && (
                  <div className="preview">
                    <p>
                      {preview.document.portfolio.name} ·{" "}
                      {preview.transaction_count} transactions ·{" "}
                      {preview.document.portfolio.positions.length} holdings
                    </p>
                    <button
                      className="accent"
                      disabled={busy}
                      onClick={() =>
                        void act(async () => {
                          const saved = await api<Document>(
                            `/portfolio/import/commit?${scope(identity, id || "default")}`,
                            send({
                              mode,
                              data: JSON.parse(importData),
                              preview_hash: preview.preview_hash,
                              expected_revision: preview.expected_revision,
                            }),
                          );
                          setDialog("");
                          await reloadList(saved.id);
                          if (saved.id === id) await refresh();
                        })
                      }
                    >
                      Commit import
                    </button>
                  </div>
                )}
              </>
            )}
          </section>
        </div>
      )}
    </>
  );
}
