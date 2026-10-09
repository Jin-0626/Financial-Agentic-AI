import { desktop, openPortfolioFile } from "./desktop";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { amount, api, Identity, number, scope, send } from "./api";
type Holding = {
  symbol: string;
  quantity: string;
  average_cost: string;
  market_value?: string;
  cost_basis?: string;
  unrealized_gain?: string;
  weight_percent?: string;
  price?: string;
  name?: string;
  sector?: string;
  market_state?: string;
  as_of?: number;
  change_percent?: number;
};
type Transaction = {
  id: string;
  symbol: string;
  side: string;
  quantity: string;
  price: string;
  date: string;
  realized_gain?: string;
  notes?: string;
};
type Document = {
  id: string;
  revision: number;
  legacy_opening_unknown?: boolean;
  portfolio: {
    name: string;
    owner?: string;
    currency: string;
    benchmark: string;
    period: string;
    positions: Holding[];
  };
  transactions: Transaction[];
  transaction_corrections?: {
    previous: Transaction;
    replacement: Transaction;
    corrected_at: string;
  }[];
};
type Performance = {
  dates: string[];
  portfolio: number[];
  benchmark: number[];
  method: string;
  limitations: string;
  portfolio_return_percent: number;
  benchmark_return_percent: number;
};
type Summary = {
  sectors?: { name: string; market_value: string; weight_percent: string }[];
  revision: number;
  positions: Holding[];
  market_value: string | null;
  cost_basis: string | null;
  unrealized_gain: string | null;
  realized_gain?: string;
  dividends?: string;
  currency: string;
  fetched_at: string;
  comparison?: Performance;
  projection?: Performance;
  comparison_error?: string;
  errors: { symbol?: string; error: string }[];
  history: { date: string; market_value: string; cost_basis: string }[];
  performance_reset_date?: string;
};
type Preview = {
  preview_hash: string;
  expected_revision: number;
  transaction_count: number;
  document: Document;
};
export function PortfolioPage({ identity }: { identity: Identity }) {
  const savedKey = `portfolio:${identity.org}:${identity.user}`;
  const [list, setList] = useState<
    { id: string; name: string; currency: string }[]
  >([]);
  const [id, setId] = useState("");
  const [doc, setDoc] = useState<Document | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState("Overview");
  const [view, setView] = useState("table");
  const [dialog, setDialog] = useState("");
  const [editingTrade, setEditingTrade] = useState<Transaction | null>(null);
  const [name, setName] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [benchmark, setBenchmark] = useState("SPY");
  const [query, setQuery] = useState("");
  const [symbols, setSymbols] = useState<
    { symbol: string; name: string; exchange: string }[]
  >([]);
  const [symbol, setSymbol] = useState("");
  const [side, setSide] = useState("BUY");
  const [quantity, setQuantity] = useState("");
  const [price, setPrice] = useState("");
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [notes, setNotes] = useState("");
  const [importData, setImportData] = useState("");
  const [mode, setMode] = useState("New");
  const [preview, setPreview] = useState<Preview | null>(null);
  const tradeId = useRef(crypto.randomUUID());
  const generation = useRef(0);
  const refreshing = useRef<string | null>(null);
  const reloadList = useCallback(
    async (select?: string) => {
      const data = await api<{ portfolios: typeof list }>(
        `/portfolio/list?${scope(identity)}`,
      );
      setList(data.portfolios);
      const choice = select || localStorage.getItem(savedKey);
      setId(
        data.portfolios.find((p) => p.id === choice)?.id ||
          data.portfolios[0]?.id ||
          "",
      );
    },
    [identity, savedKey],
  );
  useEffect(() => {
    void reloadList().catch((e) => setError(e.message));
  }, [reloadList]);
  const refresh = useCallback(async () => {
    if (!id || refreshing.current === id) return;
    refreshing.current = id;
    setBusy(true);
    setError("");
    const ticket = generation.current;
    try {
      const document = await api<Document>(`/portfolio?${scope(identity, id)}`);
      if (ticket !== generation.current) return;
      setDoc(document);
      const valuation = await api<Summary>(
        `/portfolio/summary?${scope(identity, id)}`,
      );
      if (ticket !== generation.current) return;
      setSummary(valuation);
      setDoc({ ...document, revision: valuation.revision });
    } catch (e) {
      if (ticket === generation.current) setError((e as Error).message);
    } finally {
      if (refreshing.current === id) {
        refreshing.current = null;
        setBusy(false);
      }
    }
  }, [id, identity]);
  useEffect(() => {
    generation.current++;
    setDoc(null);
    setSummary(null);
    if (id) localStorage.setItem(savedKey, id);
    void refresh();
    return () => {
      generation.current++;
      refreshing.current = null;
    };
  }, [id, savedKey, refresh]);
  useEffect(() => {
    const tick = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    const timer = setInterval(tick, 300000);
    document.addEventListener("visibilitychange", tick);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [refresh]);
  async function act(work: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function open(value: string) {
    tradeId.current = crypto.randomUUID();
    setError("");
    setDialog(value);
    setEditingTrade(null);
    setPreview(null);
    setSymbol("");
    setQuery("");
    setSymbols([]);
    setQuantity("");
    setPrice("");
    setName(doc?.portfolio.name || "");
    setCurrency(doc?.portfolio.currency || "USD");
    setBenchmark(doc?.portfolio.benchmark || "SPY");
  }
  function selectCorrection(trade: Transaction) {
    setEditingTrade(trade);
    setSymbol(trade.symbol);
    setSide(trade.side);
    setQuantity(trade.quantity);
    setPrice(trade.price);
    setDate(trade.date);
    setNotes(trade.notes || "");
  }
  function editHolding(holding: Holding) {
    const trades =
      doc?.transactions.filter((t) => t.symbol === holding.symbol) || [];
    if (!trades.length) return;
    open("Edit holding");
    selectCorrection(trades.find((t) => t.side === "BUY") || trades[0]);
  }
  const currencyCode = doc?.portfolio.currency || "USD";
  const perf = summary?.comparison || summary?.projection;
  const chart =
    perf?.dates.map((day, i) => ({
      date: day,
      Portfolio: perf.portfolio[i],
      Benchmark: perf.benchmark[i],
    })) || [];
  const positions = summary?.positions.length
    ? summary.positions
    : doc?.portfolio.positions || [];
  const sectors = summary?.sectors || [];
  const holdingsTable = (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {[
              "Symbol / Company",
              "Quantity",
              "Average cost",
              "Quote",
              "Market value",
              "P&L",
              "Weight",
              "Market / As of",
              "Actions",
            ].map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr key={p.symbol}>
              <td>
                <b>{p.symbol}</b>
                <small>{p.name}</small>
              </td>
              <td>{p.quantity}</td>
              <td>{amount(p.average_cost, currencyCode)}</td>
              <td>{amount(p.price, currencyCode)}</td>
              <td>{amount(p.market_value, currencyCode)}</td>
              <td
                className={
                  (number(p.unrealized_gain) || 0) < 0 ? "negative" : "positive"
                }
              >
                {amount(p.unrealized_gain, currencyCode)}
              </td>
              <td>
                {p.weight_percent
                  ? `${Number(p.weight_percent).toFixed(1)}%`
                  : "—"}
              </td>
              <td>
                {p.market_state || "Unavailable"}
                <small>
                  {p.as_of
                    ? new Date(p.as_of * 1000).toLocaleString()
                    : "Quote time not supplied"}
                </small>
              </td>
              <td>
                <button
                  disabled={
                    busy ||
                    !doc?.transactions.some((t) => t.symbol === p.symbol)
                  }
                  aria-label={`Edit holding ${p.symbol}`}
                  title={
                    doc?.transactions.some((t) => t.symbol === p.symbol)
                      ? "Correct a recorded transaction"
                      : "No recorded transactions to edit for this legacy holding"
                  }
                  onClick={() => editHolding(p)}
                >
                  Edit
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
  return (
    <main>
      <div className="page-title">
        <div>
          <span className="eyebrow">ASSET MANAGEMENT</span>
          <h1>Portfolio</h1>
        </div>
        <span className="tag">{currencyCode} · LIVE QUOTES</span>
      </div>
      <div className="commandbar">
        <select
          aria-label="Selected portfolio"
          value={id}
          onChange={(e) => setId(e.target.value)}
        >
          <option value="">Select portfolio</option>
          {list.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} · {p.currency}
            </option>
          ))}
        </select>
        <button onClick={() => open("New portfolio")}>+ New portfolio</button>
        <button disabled={!doc || busy} onClick={() => open("Record trade")}>
          Record trade
        </button>
        <button onClick={() => open("Import JSON")}>Import</button>
        <button
          disabled={!doc || busy}
          onClick={() =>
            void act(async () => {
              const data = await api(
                `/portfolio/export?${scope(identity, id)}`,
              );
              const url = URL.createObjectURL(
                new Blob([JSON.stringify(data, null, 2)], {
                  type: "application/json",
                }),
              );
              const a = document.createElement("a");
              a.href = url;
              a.download = `${doc?.portfolio.name || "portfolio"}.json`;
              a.click();
              URL.revokeObjectURL(url);
            })
          }
        >
          Export
        </button>
        <button disabled={!doc || busy} onClick={() => open("Settings")}>
          Settings
        </button>
        <button
          disabled={!id || busy}
          onClick={() => void refresh()}
          className="accent"
        >
          {busy ? "Updating…" : "↻ Refresh"}
        </button>
      </div>
      {error && (
        <div role="alert" className="error">
          {error}
        </div>
      )}
      {!id && !busy && (
        <div className="empty">
          <h2>Your portfolio workspace</h2>
          <p>
            Create a portfolio or import a Financial transaction file to begin.
          </p>
          <button className="accent" onClick={() => open("New portfolio")}>
            Create portfolio
          </button>
        </div>
      )}
      {doc && (
        <>
          <section className="stats">
            {[
              ["Market value", summary?.market_value],
              ["Cost basis", summary?.cost_basis],
              ["Unrealized P&L", summary?.unrealized_gain],
              ["Realized P&L", summary?.realized_gain],
              ["Dividends", summary?.dividends],
            ].map(([label, value]) => (
              <div key={label} className="stat">
                <span>{label}</span>
                <strong
                  className={
                    (number(value) || 0) < 0
                      ? "negative"
                      : label?.includes("P&L")
                        ? "positive"
                        : ""
                  }
                >
                  {amount(value, currencyCode)}
                </strong>
              </div>
            ))}
            <div className="stat">
              <span>Holdings</span>
              <strong>{doc.portfolio.positions.length}</strong>
            </div>
          </section>
          {doc.legacy_opening_unknown && (
            <p className="notice">
              Legacy opening holdings are preserved. Their purchase dates are
              unknown; transaction-only export requires reconciliation.
            </p>
          )}
          {summary?.errors.map((e, i) => (
            <p className="error" key={i}>
              {e.symbol}: {e.error}
            </p>
          ))}
          <div className="tabs" role="tablist">
            {[
              "Overview",
              "Holdings",
              "Performance",
              "Sectors",
              "Transactions",
              "History",
            ].map((t) => (
              <button
                role="tab"
                aria-selected={tab === t}
                className={tab === t ? "selected" : ""}
                key={t}
                onClick={() => setTab(t)}
              >
                {t}
              </button>
            ))}
          </div>
          <div className={tab === "Overview" ? "dashboard" : ""}>
            {(tab === "Holdings" || tab === "Overview") && (
              <section className="panel">
                <div className="panel-head">
                  <h2>
                    Holdings <small>{doc.portfolio.name}</small>
                  </h2>
                  <div>
                    <button
                      onClick={() => {
                        setView("table");
                        setTab("Holdings");
                      }}
                    >
                      Table
                    </button>
                    <button
                      onClick={() => {
                        setView("heatmap");
                        setTab("Holdings");
                      }}
                    >
                      Heatmap
                    </button>
                  </div>
                </div>
                {!positions.length ? (
                  <p className="empty">
                    Record a BUY transaction to add your first holding.
                  </p>
                ) : view === "heatmap" || tab === "Overview" ? (
                  <div className="heatmap">
                    {positions.map((p) => (
                      <div
                        key={p.symbol}
                        className={(p.change_percent || 0) < 0 ? "down" : "up"}
                        style={{
                          flexGrow: Math.max(1, number(p.weight_percent) || 1),
                        }}
                      >
                        <b>{p.symbol}</b>
                        <strong>
                          {p.change_percent == null
                            ? "—"
                            : `${p.change_percent.toFixed(2)}%`}
                        </strong>
                        <small>{amount(p.market_value, currencyCode)}</small>
                      </div>
                    ))}
                  </div>
                ) : (
                  holdingsTable
                )}
              </section>
            )}
            {(tab === "Performance" || tab === "Overview") && (
              <section className="panel">
                <div className="panel-head">
                  <h2>
                    {perf?.method === "projection"
                      ? "Fixed-holdings projection"
                      : "Recorded performance"}
                  </h2>
                  <span>Benchmark · {doc.portfolio.benchmark}</span>
                </div>
                {summary?.comparison_error && (
                  <p className="notice">{summary.comparison_error}</p>
                )}
                {chart.length > 0 ? (
                  <>
                    <div className="chart">
                      <ResponsiveContainer>
                        <LineChart data={chart}>
                          <CartesianGrid
                            stroke="#252b35"
                            strokeDasharray="3 3"
                          />
                          <XAxis dataKey="date" minTickGap={55} />
                          <YAxis domain={["auto", "auto"]} />
                          <Tooltip />
                          <Line
                            type="monotone"
                            dataKey="Portfolio"
                            stroke="#ff9f43"
                            dot={false}
                          />
                          <Line
                            type="monotone"
                            dataKey="Benchmark"
                            stroke="#6aa5ff"
                            dot={false}
                          />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                    <p className="notice">{perf?.limitations}</p>
                  </>
                ) : (
                  <p className="empty">
                    Performance is unavailable until valid prices and common
                    valuation dates exist.
                  </p>
                )}
              </section>
            )}
            {(tab === "Sectors" || tab === "Overview") && (
              <section className="panel">
                <h2>Sector allocation</h2>
                {sectors.length ? (
                  sectors.map(({ name: sector, market_value: value }) => (
                    <div className="sector" key={sector}>
                      <span>{sector}</span>
                      <meter
                        min={0}
                        max={Math.max(1, number(summary?.market_value) || 1)}
                        value={Number(value)}
                      />
                      <b>{amount(value, currencyCode)}</b>
                    </div>
                  ))
                ) : (
                  <p className="empty">Allocation appears after quotes load.</p>
                )}
              </section>
            )}
          </div>
          {tab === "Overview" && (
            <section className="panel">
              <h2>Positions</h2>
              {holdingsTable}
            </section>
          )}
          {tab === "Transactions" && (
            <section className="panel">
              <h2>Trade records</h2>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th>Symbol</th>
                      <th>Type</th>
                      <th>Quantity</th>
                      <th>Price</th>
                      <th>Realized P&L</th>
                      <th>Notes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {doc.transactions.map((t) => (
                      <tr key={t.id}>
                        <td>{t.date}</td>
                        <td>{t.symbol}</td>
                        <td
                          className={
                            t.side === "SELL" ? "negative" : "positive"
                          }
                        >
                          {t.side}
                        </td>
                        <td>{t.quantity}</td>
                        <td>{amount(t.price, currencyCode)}</td>
                        <td>{amount(t.realized_gain, currencyCode)}</td>
                        <td>{t.notes}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
          {tab === "History" && (
            <section className="panel">
              <h2>Saved valuation history</h2>
              {summary?.performance_reset_date && (
                <p className="notice">
                  Transactions corrected on {summary.performance_reset_date}.
                  Earlier valuations are retained for reference and excluded
                  from recorded performance.
                </p>
              )}
              <table>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Market value</th>
                    <th>Cost basis</th>
                  </tr>
                </thead>
                <tbody>
                  {summary?.history.map((p) => (
                    <tr key={p.date}>
                      <td>{p.date}</td>
                      <td>{amount(p.market_value, currencyCode)}</td>
                      <td>{amount(p.cost_basis, currencyCode)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!!doc.transaction_corrections?.length && (
                <>
                  <h3>Recent transaction corrections</h3>
                  <p className="notice">
                    Last 20 corrections. Original recorded values are retained.
                  </p>
                  <table>
                    <thead>
                      <tr>
                        <th>Corrected (UTC)</th>
                        <th>Symbol / Type</th>
                        <th>Original quantity @ price</th>
                        <th>Corrected quantity @ price</th>
                      </tr>
                    </thead>
                    <tbody>
                      {doc.transaction_corrections
                        .slice(-20)
                        .reverse()
                        .map((entry, index) => (
                          <tr key={index}>
                            <td>
                              {entry.corrected_at
                                .slice(0, 19)
                                .replace("T", " ")}
                            </td>
                            <td>
                              {entry.previous.symbol} / {entry.previous.side}
                            </td>
                            <td>
                              {entry.previous.quantity} @ {entry.previous.price}
                            </td>
                            <td>
                              {entry.replacement.quantity} @{" "}
                              {entry.replacement.price}
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </>
              )}
            </section>
          )}
          <footer className="statusbar">
            <span>
              {positions.length} HOLDINGS · {doc.transactions.length}{" "}
              TRANSACTIONS
            </span>
            <span>
              Yahoo Finance ·{" "}
              {summary?.fetched_at
                ? new Date(summary.fetched_at).toLocaleTimeString()
                : "Awaiting quotes"}{" "}
              · Refresh every 5 min while visible
            </span>
          </footer>
        </>
      )}

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
    </main>
  );
}
