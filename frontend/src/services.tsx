import { useDesktopView } from "./view-settings";
import { StockLabel } from "./stock-label";
import { DataTable, EquityChart } from "./shared-ui";
import { invoke } from "@tauri-apps/api/core";
import { useEffect, useRef, useState } from "react";
import { price, symbol } from "./bridge";
import { PortfolioEditor } from "./portfolio-editor";

type Holding = {
  symbol: string;
  name?: string;
  quantity: string;
  average_cost: string;
  market_value?: string;
  unrealized_gain?: string;
};
export type Portfolio = {
  name: string;
  owner: string;
  currency: string;
  benchmark: string;
  period: string;
  positions: Holding[];
};
export type Document = {
  id: string;
  revision: number;
  portfolio: Portfolio;
  transactions: {
    id: string;
    symbol: string;
    side: string;
    quantity: string;
    price: string;
    date: string;
  }[];
};
type Summary = {
  revision: number;
  market_value: string | null;
  cost_basis: string | null;
  unrealized_gain: string | null;
  currency: string;
  positions: Holding[];
  comparison?: {
    dates: string[];
    portfolio: number[];
    benchmark: number[];
    method: string;
    limitations: string;
  };
  errors: { error: string }[];
};
type Trace = {
  id: string;
  thread_id: string;
  name: string;
  status: string;
  duration_ms: number;
  token_count: number;
  calls?: {
    id: string;
    name: string;
    status: string;
    input: unknown;
    output: unknown;
    error?: string;
  }[];
};
type Catalog = {
  strategies: {
    id: string;
    name: string;
    description: string;
    parameters: Record<
      string,
      { default?: number; minimum?: number; maximum?: number }
    >;
  }[];
  execution: string;
};
type BacktestResult = {
  result: {
    initial_cash: string;
    final_equity: string;
    total_return: number;
    equity: { date: string; equity: string }[];
    trades: {
      date: string;
      side: string;
      quantity: string;
      price: string;
      cost: string;
    }[];
    assumptions: string[];
    metrics: {
      maximum_drawdown: number;
      trade_count: number;
      total_costs: string;
    };
  };
  provenance: { currency: string; engine: string };
};
type ServiceRequest =
  | { type: "portfolio_list" | "threads" | "traces" | "backtest_catalog" }
  | {
      type:
        "portfolio_load" | "portfolio_summary" | "portfolio_export" | "trace";
      id: string;
    }
  | { type: "history"; thread: string }
  | { type: "backtest_history"; symbol: string; start: string; end: string }
  | {
      type: "backtest_run";
      input: {
        strategy: string;
        prices: number[];
        dates: string[];
        currency: string;
        initial_cash: string;
        cost_bps: number;
        fast_window: number;
        slow_window: number;
        rsi_window: number;
        lower: number;
        upper: number;
      };
    };
const service = <T,>(request: ServiceRequest) =>
  invoke<T>("terminal_service", { request });
export function PortfolioScreen() {
  const { view, update } = useDesktopView();
  const [list, setList] = useState<
    { id: string; name: string; currency: string }[]
  >([]);
  const [id, setId] = useState("");
  const [document, setDocument] = useState<Document | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [exported, setExported] = useState("");
  useEffect(() => {
    let active = true;
    service<{ portfolios: typeof list }>({ type: "portfolio_list" })
      .then((result) => {
        if (active) {
          setList(result.portfolios);
          setId((previous) => previous || result.portfolios[0]?.id || "");
        }
      })
      .catch((error) => active && setError(String(error)));
    return () => {
      active = false;
    };
  }, [revision]);
  useEffect(() => {
    let active = true;
    setDocument(null);
    setSummary(null);
    setExported("");
    if (id) {
      setError(null);
      void service<Summary>({ type: "portfolio_summary", id })
        .then(async (summary) => {
          const document = await service<Document>({
            type: "portfolio_load",
            id,
          });
          if (summary.revision !== document.revision)
            throw new Error(
              "Portfolio changed while refreshing. Refresh again.",
            );
          return [document, summary] as const;
        })
        .then(([document, summary]) => {
          if (active) {
            setDocument(document);
            setSummary(summary);
          }
        })
        .catch((error) => active && setError(String(error)));
    }
    return () => {
      active = false;
    };
  }, [id, revision]);
  return (
    <section>
      <PortfolioEditor
        key={id || "new"}
        document={document}
        onSaved={(newId) => {
          setId(newId);
          setRevision((value) => value + 1);
        }}
      />
      <div className="feature-toolbar">
        <select
          aria-label="Selected portfolio"
          value={id}
          onChange={(event) => setId(event.target.value)}
        >
          <option value="">Choose portfolio</option>
          {list.map((item) => (
            <option value={item.id} key={item.id}>
              {item.name} · {item.currency}
            </option>
          ))}
        </select>
        <button onClick={() => setRevision((value) => value + 1)}>
          Refresh portfolios
        </button>
        <button
          disabled={!document}
          onClick={() =>
            void service<unknown>({ type: "portfolio_export", id })
              .then((result) => setExported(JSON.stringify(result, null, 2)))
              .catch((error) => setError(String(error)))
          }
        >
          Export JSON
        </button>
      </div>
      {error && (
        <p role="alert" className="negative">
          {error}
        </p>
      )}
      {!id && !error && (
        <p>
          No saved portfolios. Portfolios are stored locally in backend SQLite.
        </p>
      )}
      {document && (
        <>
          <h3>
            {document.portfolio.name} · revision {document.revision}
          </h3>
          <div className="metrics">
            {[
              ["Market value", summary?.market_value],
              ["Cost basis", summary?.cost_basis],
              ["Unrealized gain", summary?.unrealized_gain],
            ].map(([label, value]) => (
              <article className="metric" key={label}>
                <span>{label}</span>
                <strong>
                  {price(value)} {document.portfolio.currency}
                </strong>
              </article>
            ))}
          </div>
          <div
            className="page-tabs"
            role="tablist"
            aria-label="Portfolio sections"
          >
            {(["holdings", "transactions", "performance"] as const).map(
              (tab) => (
                <button
                  role="tab"
                  aria-selected={view.portfolio_tab === tab}
                  key={tab}
                  onClick={() => update({ portfolio_tab: tab })}
                >
                  {tab[0].toUpperCase() + tab.slice(1)}
                </button>
              ),
            )}
          </div>
          {view.portfolio_tab === "holdings" && (
            <DataTable
              columns={[
                "Symbol",
                "Quantity",
                "Average cost",
                "Market value",
                "Unrealized gain",
              ]}
              rows={(summary?.positions ?? document.portfolio.positions).map(
                (row) => [
                  <StockLabel code={row.symbol} name={row.name} />,
                  row.quantity,
                  row.average_cost,
                  row.market_value,
                  row.unrealized_gain,
                ],
              )}
            />
          )}
          {view.portfolio_tab === "transactions" && (
            <>
              <h3>Transactions</h3>
              <DataTable
                columns={["Date", "Symbol", "Side", "Quantity", "Price"]}
                rows={document.transactions.map((row) => [
                  row.date,
                  <StockLabel
                    code={row.symbol}
                    name={
                      summary?.positions.find(
                        (position) => position.symbol === row.symbol,
                      )?.name
                    }
                  />,
                  row.side,
                  row.quantity,
                  row.price,
                ])}
              />
            </>
          )}
          {view.portfolio_tab === "performance" && summary?.comparison && (
            <>
              <h3>Performance</h3>
              <p>
                {summary.comparison.method} · {summary.comparison.limitations}
              </p>
              <DataTable
                columns={["Date", "Portfolio", "Benchmark"]}
                rows={summary.comparison.dates.map((day, i) => [
                  day,
                  price(summary.comparison?.portfolio[i]),
                  price(summary.comparison?.benchmark[i]),
                ])}
              />
            </>
          )}
          {view.portfolio_tab === "performance" && !summary?.comparison && (
            <div className="empty">
              Performance comparison unavailable for this portfolio.
            </div>
          )}
          {summary?.errors.map((item, i) => (
            <p className="negative" key={i}>
              {item.error}
            </p>
          ))}
        </>
      )}
      {exported && (
        <label>
          Exported document
          <textarea
            aria-label="Exported portfolio JSON"
            readOnly
            value={exported}
            rows={15}
          />
        </label>
      )}
    </section>
  );
}
export { ResearchHistoryScreen } from "./research-screen";
export function TracesScreen() {
  const [traces, setTraces] = useState<Trace[]>([]),
    [detail, setDetail] = useState<Trace | null>(null),
    [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const generation = useRef(0);
  useEffect(() => {
    let active = true;
    service<{ traces: Trace[] }>({ type: "traces" })
      .then((value) => active && setTraces(value.traces))
      .catch((error) => active && setError(String(error)));
    return () => {
      active = false;
      generation.current++;
    };
  }, [revision]);
  return (
    <section className="trace-workspace">
      <aside>
        <button onClick={() => setRevision((value) => value + 1)}>
          Refresh traces
        </button>
        {error && <p role="alert">{error}</p>}
        <div className="trace-list">
          {traces.map((trace) => (
            <button
              key={trace.id}
              onClick={() => {
                const ticket = ++generation.current;
                void service<Trace>({ type: "trace", id: trace.id })
                  .then(
                    (value) =>
                      ticket === generation.current && setDetail(value),
                  )
                  .catch(
                    (error) =>
                      ticket === generation.current && setError(String(error)),
                  );
              }}
            >
              {trace.name || trace.id} · {trace.status} · {trace.duration_ms}ms
              · {trace.token_count} tokens
            </button>
          ))}
        </div>
      </aside>
      {detail && (
        <section className="trace-detail">
          <h3>{detail.thread_id}</h3>
          {detail.calls?.map((call) => (
            <details key={call.id}>
              <summary>
                {call.name} · {call.status}
              </summary>
              <h4>Input</h4>
              <pre>{JSON.stringify(call.input, null, 2)}</pre>
              <h4>Output</h4>
              <pre>{JSON.stringify(call.output, null, 2)}</pre>
              {call.error && <p className="negative">{call.error}</p>}
            </details>
          ))}
        </section>
      )}
    </section>
  );
}
export function BacktestingScreen({ selected }: { selected: string | null }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null),
    [strategy, setStrategy] = useState("buy_and_hold");
  const [ticker, setTicker] = useState(
    selected?.startsWith("yahoo:") ? symbol(selected) : "AAPL",
  );
  const [start, setStart] = useState("2025-01-01"),
    [end, setEnd] = useState(new Date().toISOString().slice(0, 10));
  const [rows, setRows] = useState<{ date: string; close: number }[]>([]),
    [currency, setCurrency] = useState("USD"),
    [source, setSource] = useState("");
  const [capital, setCapital] = useState("10000"),
    [cost, setCost] = useState(10),
    [fast, setFast] = useState(5),
    [slow, setSlow] = useState(20),
    [rsi, setRsi] = useState(14),
    [lower, setLower] = useState(30),
    [upper, setUpper] = useState(70);
  const [result, setResult] = useState<BacktestResult | null>(null),
    [error, setError] = useState<string | null>(null),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    service<Catalog>({ type: "backtest_catalog" })
      .then((value) => active && setCatalog(value))
      .catch((error) => active && setError(String(error)));
    return () => {
      active = false;
    };
  }, []);
  async function load() {
    setBusy(true);
    setRows([]);
    setResult(null);
    setError(null);
    try {
      const value = await service<{
        status: string;
        data: typeof rows | null;
        currency: string;
        source: string;
        coverage: string;
        error: string | null;
      }>({ type: "backtest_history", symbol: ticker.trim(), start, end });
      if (value.status !== "ok" || !value.data)
        throw new Error(value.error ?? value.coverage);
      setRows(value.data);
      setCurrency(value.currency);
      setSource(`${value.source} · ${value.coverage}`);
    } catch (error) {
      setError(String(error));
    } finally {
      setBusy(false);
    }
  }
  async function run() {
    setBusy(true);
    setError(null);
    try {
      setResult(
        await service<BacktestResult>({
          type: "backtest_run",
          input: {
            strategy,
            prices: rows.map((row) => row.close),
            dates: rows.map((row) => row.date),
            currency,
            initial_cash: capital,
            cost_bps: cost,
            fast_window: fast,
            slow_window: slow,
            rsi_window: rsi,
            lower,
            upper,
          },
        }),
      );
    } catch (error) {
      setError(String(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section>
      <p>{catalog?.execution}</p>
      <div className="feature-toolbar">
        <label>
          Symbol
          <input
            aria-label="Backtest symbol"
            value={ticker}
            maxLength={32}
            onChange={(event) => {
              setTicker(event.target.value);
              setRows([]);
            }}
          />
        </label>
        <label>
          Start
          <input
            type="date"
            value={start}
            onChange={(event) => {
              setStart(event.target.value);
              setRows([]);
            }}
          />
        </label>
        <label>
          End
          <input
            type="date"
            value={end}
            onChange={(event) => {
              setEnd(event.target.value);
              setRows([]);
            }}
          />
        </label>
        <button
          disabled={busy || !start || !end || start >= end}
          onClick={() => void load()}
        >
          Load adjusted history
        </button>
      </div>
      <p>
        {rows.length} observations · {currency} · {source}
      </p>
      <div className="feature-toolbar">
        <select
          aria-label="Backtest strategy"
          value={strategy}
          onChange={(event) => setStrategy(event.target.value)}
        >
          {catalog?.strategies.map((row) => (
            <option key={row.id} value={row.id}>
              {row.name}
            </option>
          ))}
        </select>
        <label>
          Initial cash
          <input
            value={capital}
            onChange={(event) => setCapital(event.target.value)}
          />
        </label>
        {(
          [
            ["Cost bps", cost, setCost],
            ["Fast window", fast, setFast],
            ["Slow window", slow, setSlow],
            ["RSI window", rsi, setRsi],
            ["Lower RSI", lower, setLower],
            ["Upper RSI", upper, setUpper],
          ] as const
        ).map(([label, value, setter]) => (
          <label key={label}>
            {label}
            <input
              type="number"
              value={value}
              onChange={(event) => setter(Number(event.target.value))}
            />
          </label>
        ))}
        <button
          disabled={busy || rows.length < 3 || !catalog}
          onClick={() => void run()}
        >
          {busy ? "Working…" : "Run backtest"}
        </button>
      </div>
      {error && (
        <p role="alert" className="negative">
          {error}
        </p>
      )}
      {result && (
        <>
          <h3>
            {result.provenance.engine} · {result.provenance.currency}
          </h3>
          <p>
            Final equity {result.result.final_equity} · return{" "}
            {price(result.result.total_return * 100)}% · max drawdown{" "}
            {price(result.result.metrics.maximum_drawdown * 100)}% ·{" "}
            {result.result.metrics.trade_count} trades
          </p>
          {result.result.assumptions.map((line) => (
            <p key={line}>{line}</p>
          ))}
          <DataTable
            columns={["Date", "Equity"]}
            rows={result.result.equity.map((row) => [row.date, row.equity])}
          />
          <EquityChart
            rows={result.result.equity}
            currency={result.provenance.currency}
          />
          <h3>Trades</h3>
          <DataTable
            columns={["Date", "Side", "Quantity", "Price", "Cost"]}
            rows={result.result.trades.map((row) => [
              row.date,
              row.side,
              row.quantity,
              row.price,
              row.cost,
            ])}
          />
        </>
      )}
    </section>
  );
}
