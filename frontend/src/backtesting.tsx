import { useEffect, useRef, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, amount, Identity, scope, send } from "./api";

type Row = { date: string; close: number };
type Strategy = {
  id: string;
  name: string;
  description: string;
  parameters: Record<
    string,
    { title?: string; default?: number; minimum?: number; maximum?: number }
  >;
};
type Catalog = { strategies: Strategy[]; execution: string };
type Trade = {
  date: string;
  side: string;
  quantity: string;
  price: string;
  cost: string;
};
type Result = {
  status: string;
  provenance: { currency: string; engine: string };
  result: {
    strategy: string;
    initial_cash: string;
    final_equity: string;
    total_return: number;
    equity: { date: string; equity: string }[];
    trades: Trade[];
    assumptions: string[];
    metrics: {
      maximum_drawdown: number;
      trade_count: number;
      total_costs: string;
      open_position: boolean;
    };
  };
};
type Completed = {
  response: Result;
  inputs: Record<string, unknown>;
  source: string;
  label: string;
};
const tabs = [
  "Summary",
  "Equity Curve",
  "Metrics",
  "Trades",
  "Raw JSON",
] as const;
const percent = (n: number) => `${(n * 100).toFixed(2)}%`;

function parseCSV(text: string): Row[] {
  if (new TextEncoder().encode(text).length > 1048576)
    throw new Error("CSV must be smaller than 1 MiB.");
  const lines = text
    .trim()
    .replace(/^\uFEFF/, "")
    .split(/\r?\n/);
  const cells = (line: string) =>
    line.split(",").map((value) => value.trim().replace(/^"(.*)"$/, "$1"));
  const header = cells(lines.shift() || "").map((value) => value.toLowerCase());
  const dateColumn = header.indexOf("date"),
    closeColumn = header.indexOf("close");
  if (
    dateColumn < 0 ||
    closeColumn < 0 ||
    new Set(header).size !== header.length
  )
    throw new Error(
      "CSV needs date and close columns, with dates in YYYY-MM-DD format.",
    );
  if (lines.length < 3 || lines.length > 10000)
    throw new Error("Provide 3 to 10,000 price observations.");
  return lines.map((line, index) => {
    const parts = cells(line),
      date = parts[dateColumn],
      rawClose = parts[closeColumn];
    if (parts.length !== header.length)
      throw new Error(
        `CSV line ${index + 2} has a different number of columns than its header.`,
      );
    const timestamp = new Date(`${date}T00:00:00Z`);
    const close = Number(rawClose);
    if (
      !/^\d{4}-\d{2}-\d{2}$/.test(date || "") ||
      !Number.isFinite(timestamp.getTime()) ||
      timestamp.toISOString().slice(0, 10) !== date ||
      !rawClose ||
      !/^[+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(rawClose) ||
      !Number.isFinite(close) ||
      close <= 0
    )
      throw new Error(
        `Invalid date or positive closing price on CSV line ${index + 2}.`,
      );
    if (index && date <= cells(lines[index - 1])[dateColumn])
      throw new Error(
        "Dates must be unique and increasing; no rows are sorted or filled automatically.",
      );
    return { date, close };
  });
}

function download(name: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function BacktestingPage({ identity }: { identity: Identity }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null),
    [catalogError, setCatalogError] = useState("");
  const [strategy, setStrategy] = useState("buy_and_hold"),
    [params, setParams] = useState<Record<string, number | string>>({});
  const [mode, setMode] = useState("CSV"),
    [csv, setCSV] = useState(""),
    [rows, setRows] = useState<Row[]>([]);
  const [symbol, setSymbol] = useState("AAPL"),
    [start, setStart] = useState(""),
    [end, setEnd] = useState("");
  const [currency, setCurrency] = useState("USD"),
    [capital, setCapital] = useState("10000"),
    [cost, setCost] = useState("10");
  const [source, setSource] = useState(""),
    [loadedLabel, setLoadedLabel] = useState("");
  const [error, setError] = useState(""),
    [busy, setBusy] = useState<"history" | "run" | null>(null);
  const [completed, setCompleted] = useState<Completed | null>(null),
    [tab, setTab] = useState<(typeof tabs)[number]>("Summary");
  const [tradePage, setTradePage] = useState(0);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    api<Catalog>("/backtesting/catalog", { signal: abort.signal })
      .then(setCatalog)
      .catch((error) => {
        if (!abort.signal.aborted) setCatalogError(String(error.message));
      });
    return () => {
      abort.abort();
      controller.current?.abort();
    };
  }, []);
  const selected = catalog?.strategies.find((s) => s.id === strategy);
  const specific = Object.entries(selected?.parameters || {}).filter(
    ([key]) =>
      !["prices", "dates", "initial_cash", "cost_bps", "currency"].includes(
        key,
      ),
  );
  function resetData() {
    setRows([]);
    setSource("");
    setLoadedLabel("");
    setError("");
  }
  function acceptCSV(text: string, label: string) {
    setCSV(text);
    resetData();
    try {
      setRows(parseCSV(text));
      setSource(
        "User-supplied closing prices; adjustments must be verified by the user",
      );
      setLoadedLabel(label);
    } catch (error) {
      setError((error as Error).message);
    }
  }
  async function loadHistory() {
    setError("");
    resetData();
    setBusy("history");
    const abort = new AbortController();
    controller.current = abort;
    try {
      const data = await api<{
        status: string;
        stale?: boolean;
        error?: string;
        data: Row[];
        currency: string;
        symbol: string;
        coverage: string;
      }>("/backtesting/history", {
        ...send({ symbol: symbol.trim(), start, end }),
        signal: abort.signal,
      });
      if (data.status !== "ok" || data.stale)
        throw new Error(
          data.error ||
            "Fresh history is unavailable. Retry or import CSV data.",
        );
      if (!data.data.length)
        throw new Error("No price history is available for this date range.");
      setRows(data.data);
      setCurrency(data.currency);
      setSource(`Yahoo Finance · ${data.coverage}`);
      setLoadedLabel(data.symbol);
    } catch (error) {
      if (!abort.signal.aborted) setError((error as Error).message);
    } finally {
      if (controller.current === abort) {
        controller.current = null;
        setBusy(null);
      }
    }
  }
  async function run() {
    setError("");
    setCompleted(null);
    setBusy("run");
    setTradePage(0);
    const abort = new AbortController();
    controller.current = abort;
    const inputs = {
      prices: rows.map((row) => row.close),
      dates: rows.map((row) => row.date),
      currency,
      initial_cash: capital,
      cost_bps: Number(cost),
      strategy,
      ...Object.fromEntries(
        specific.map(([key, schema]) => [
          key,
          Number(params[key] ?? schema.default),
        ]),
      ),
    };
    try {
      if (strategy === "moving_average_crossover") {
        const fast = Number(
          params.fast_window ?? selected?.parameters.fast_window.default,
        );
        const slow = Number(
          params.slow_window ?? selected?.parameters.slow_window.default,
        );
        if (fast >= slow)
          throw new Error("Fast window must be smaller than slow window.");
        if (rows.length <= slow)
          throw new Error("Load more observations than the slow window.");
      }
      if (
        strategy === "rsi" &&
        rows.length <=
          Number(params.rsi_window ?? selected?.parameters.rsi_window.default)
      )
        throw new Error("Load more observations than the RSI window.");
      const response = await api<Result>(`/backtesting/run?${scope(identity)}`, {
        ...send(inputs),
        signal: abort.signal,
      });
      if (response.status !== "success")
        throw new Error("Backtest did not return a successful result.");
      setCompleted({ response, inputs, source, label: loadedLabel });
      setTab("Summary");
    } catch (error) {
      if (!abort.signal.aborted) setError((error as Error).message);
    } finally {
      if (controller.current === abort) {
        controller.current = null;
        setBusy(null);
      }
    }
  }
  function cancel() {
    controller.current?.abort();
    setError("Request cancelled.");
  }
  const result = completed?.response.result,
    resultCurrency = completed?.response.provenance.currency || currency;
  let peak = Number(result?.initial_cash || 0);
  const chart =
    result?.equity.map((row) => {
      const equity = Number(row.equity);
      peak = Math.max(peak, equity);
      return {
        date: row.date,
        equity,
        drawdown: peak ? (equity / peak - 1) * 100 : 0,
      };
    }) || [];
  const metricRows = result
    ? [
        ["Initial capital", amount(result.initial_cash, resultCurrency)],
        ["Final equity", amount(result.final_equity, resultCurrency)],
        ["Total return", percent(result.total_return)],
        ["Maximum drawdown", percent(result.metrics.maximum_drawdown)],
        ["Trade executions", String(result.metrics.trade_count)],
        [
          "Transaction costs",
          amount(result.metrics.total_costs, resultCurrency),
        ],
        [
          "Final position",
          result.metrics.open_position ? "Open, marked to market" : "Cash",
        ],
      ]
    : [];
  return (
    <main className="backtest-page">
      <div className="bt-heading">
        <div>
          <h1>Backtesting</h1>
          <p>Test a strategy against historical closing prices.</p>
        </div>
        <span role="status">
          {busy === "run"
            ? "RUNNING"
            : busy === "history"
              ? "LOADING DATA"
              : result
                ? "COMPLETED"
                : "READY"}
        </span>
      </div>
      {catalogError && (
        <p role="alert">Strategy catalog unavailable: {catalogError}</p>
      )}
      {error && (
        <p role="alert" className="bt-error">
          {error}
        </p>
      )}
      <div className="bt-layout">
        <form
          className="panel bt-config"
          onSubmit={(event) => {
            event.preventDefault();
            void run();
          }}
        >
          <fieldset disabled={!!busy}>
            <legend>Market data</legend>
            <label>
              Data source
              <select
                aria-label="Data source"
                value={mode}
                onChange={(event) => {
                  setMode(event.target.value);
                  resetData();
                }}
              >
                <option>CSV</option>
                <option>Yahoo Finance</option>
              </select>
            </label>
            {mode === "CSV" ? (
              <>
                <label>
                  Import price CSV
                  <input
                    type="file"
                    accept=".csv,text/csv"
                    onChange={async (event) => {
                      const file = event.target.files?.[0];
                      if (file) {
                        if (file.size > 1048576) {
                          resetData();
                          setError("CSV must be smaller than 1 MiB.");
                          return;
                        }
                        try {
                          acceptCSV(await file.text(), file.name);
                        } catch {
                          resetData();
                          setError("Could not read CSV file.");
                        }
                      }
                    }}
                  />
                </label>
                <label>
                  Price CSV
                  <textarea
                    aria-label="Price CSV"
                    rows={5}
                    value={csv}
                    placeholder={
                      "date,close\n2025-01-02,100\n2025-01-03,102\n2025-01-06,101"
                    }
                    onChange={(event) => {
                      setCSV(event.target.value);
                      resetData();
                    }}
                  />
                </label>
                <button
                  type="button"
                  onClick={() => acceptCSV(csv, "Pasted CSV")}
                >
                  Validate CSV
                </button>
                <p className="notice">
                  Required columns: date, close. 3–10,000 observations, unique
                  dates in increasing order. Extra unquoted columns are ignored.
                </p>
              </>
            ) : (
              <>
                <label>
                  Symbol
                  <input
                    required
                    value={symbol}
                    maxLength={32}
                    onChange={(event) => {
                      setSymbol(event.target.value);
                      resetData();
                    }}
                  />
                </label>
                <label>
                  Start date
                  <input
                    required
                    type="date"
                    value={start}
                    onChange={(event) => {
                      setStart(event.target.value);
                      resetData();
                    }}
                  />
                </label>
                <label>
                  End date
                  <input
                    required
                    type="date"
                    value={end}
                    onChange={(event) => {
                      setEnd(event.target.value);
                      resetData();
                    }}
                  />
                </label>
                <button
                  type="button"
                  disabled={!start || !end || start >= end || !symbol.trim()}
                  onClick={() => void loadHistory()}
                >
                  Load daily history
                </button>
              </>
            )}
            <label>
              Currency
              <input
                required
                value={currency}
                pattern="[A-Z]{3}"
                maxLength={3}
                readOnly={mode !== "CSV"}
                onChange={(event) =>
                  setCurrency(event.target.value.toUpperCase())
                }
              />
            </label>
          </fieldset>
          <fieldset disabled={!!busy || !catalog}>
            <legend>Strategy</legend>
            <label>
              Strategy
              <select
                aria-label="Strategy"
                value={strategy}
                onChange={(event) => {
                  setStrategy(event.target.value);
                  setParams({});
                }}
              >
                {catalog?.strategies.map((strategy) => (
                  <option key={strategy.id} value={strategy.id}>
                    {strategy.name}
                  </option>
                ))}
              </select>
            </label>
            <p className="notice">{selected?.description}</p>
            {specific.map(([key, schema]) => (
              <label key={key}>
                {schema.title || key.replaceAll("_", " ")}
                <input
                  required
                  type="number"
                  min={schema.minimum}
                  max={schema.maximum}
                  step={1}
                  value={params[key] ?? schema.default ?? ""}
                  onChange={(event) =>
                    setParams({
                      ...params,
                      [key]: event.target.value,
                    })
                  }
                />
              </label>
            ))}
          </fieldset>
          <fieldset disabled={!!busy}>
            <legend>Execution</legend>
            <label>
              Initial capital
              <input
                required
                type="number"
                min="0.00000001"
                step="any"
                value={capital}
                onChange={(event) => setCapital(event.target.value)}
              />
            </label>
            <label>
              Cost per trade (basis points)
              <input
                required
                type="number"
                min="0"
                max="1000"
                step="any"
                value={cost}
                onChange={(event) => setCost(event.target.value)}
              />
            </label>
            <p className="notice">
              10 basis points = 0.10%. Costs apply to buys and sells. Long-only,
              fractional shares; no leverage.
            </p>
          </fieldset>
          {rows.length > 0 && (
            <p className="bt-data-ready">
              {loadedLabel} · {rows.length} observations
              <br />
              {rows[0].date} — {rows[rows.length - 1].date}
            </p>
          )}
          <button
            className="bt-run"
            disabled={!!busy || !catalog || rows.length < 3}
          >
            Run backtest
          </button>
          {busy && (
            <button type="button" onClick={cancel}>
              Cancel request
            </button>
          )}
        </form>
        <section className="panel bt-results" aria-label="Backtest results">
          <div className="bt-results-header">
            <h2>Results</h2>
            <button
              disabled={!completed || !!busy}
              onClick={() =>
                download(
                  "backtest-result.json",
                  JSON.stringify(completed, null, 2),
                  "application/json",
                )
              }
            >
              Export JSON
            </button>
          </div>
          <div className="bt-tabs" role="tablist" aria-label="Result views">
            {tabs.map((name) => (
              <button
                key={name}
                role="tab"
                aria-selected={tab === name}
                aria-controls="bt-result-panel"
                id={`bt-tab-${name.replaceAll(" ", "-")}`}
                onClick={() => setTab(name)}
              >
                {name}
              </button>
            ))}
          </div>
          <div
            id="bt-result-panel"
            role="tabpanel"
            aria-labelledby={`bt-tab-${tab.replaceAll(" ", "-")}`}
          >
            {!result ? (
              <div className="bt-empty">
                <h2>Configure your first backtest</h2>
                <p>
                  Load daily history or import closing prices, choose a
                  strategy, and run.
                </p>
                <p>{catalog?.execution}</p>
              </div>
            ) : (
              <>
                <p className="notice">
                  Completed run: {completed?.label} ·{" "}
                  {result.strategy.replaceAll("_", " ")} · {resultCurrency} ·{" "}
                  {result.equity[0].date} —{" "}
                  {result.equity[result.equity.length - 1].date}
                </p>
                {tab === "Summary" && (
                  <>
                    <div className="bt-metrics">
                      {metricRows.slice(0, 6).map(([label, value]) => (
                        <article key={label}>
                          <span>{label}</span>
                          <strong>{value}</strong>
                        </article>
                      ))}
                    </div>
                    <h3>Execution assumptions</h3>
                    <ul>
                      {result.assumptions.map((text) => (
                        <li key={text}>{text}</li>
                      ))}
                    </ul>
                    <p className="notice">{completed?.source}</p>
                    <p className="notice">
                      End equity includes any open position. Returns describe
                      this price series; they are not a forecast.
                    </p>
                  </>
                )}
                {tab === "Equity Curve" && (
                  <>
                    <h3>Equity ({resultCurrency})</h3>
                    <div className="bt-chart">
                      <ResponsiveContainer>
                        <LineChart data={chart}>
                          <CartesianGrid stroke="#262626" />
                          <XAxis dataKey="date" minTickGap={55} />
                          <YAxis domain={["auto", "auto"]} />
                          <Tooltip />
                          <Line
                            type="linear"
                            dataKey="equity"
                            stroke="#ff9900"
                            dot={false}
                            isAnimationActive={false}
                          />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                    <h3>Drawdown (%)</h3>
                    <div className="bt-chart">
                      <ResponsiveContainer>
                        <LineChart data={chart}>
                          <CartesianGrid stroke="#262626" />
                          <XAxis dataKey="date" minTickGap={55} />
                          <YAxis domain={["auto", 0]} />
                          <Tooltip />
                          <Line
                            type="linear"
                            dataKey="drawdown"
                            stroke="#f87171"
                            dot={false}
                            isAnimationActive={false}
                          />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  </>
                )}
                {tab === "Metrics" && (
                  <table>
                    <thead>
                      <tr>
                        <th>Metric</th>
                        <th>Value</th>
                      </tr>
                    </thead>
                    <tbody>
                      {metricRows.map(([label, value]) => (
                        <tr key={label}>
                          <td>{label}</td>
                          <td>{value}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {tab === "Trades" && (
                  <>
                    <button
                      onClick={() =>
                        download(
                          "backtest-trades.csv",
                          [
                            "date,side,quantity,price,cost",
                            ...result.trades.map((trade) =>
                              [
                                trade.date,
                                trade.side,
                                trade.quantity,
                                trade.price,
                                trade.cost,
                              ].join(","),
                            ),
                          ].join("\n"),
                          "text/csv",
                        )
                      }
                    >
                      Export trades CSV
                    </button>
                    {!result.trades.length ? (
                      <p>No trades were triggered by this strategy.</p>
                    ) : (
                      <>
                        <div className="bt-table">
                          <table>
                            <thead>
                              <tr>
                                {[
                                  "Date",
                                  "Side",
                                  "Quantity",
                                  "Price",
                                  "Cost",
                                ].map((name) => (
                                  <th key={name}>{name}</th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {result.trades
                                .slice(tradePage * 50, (tradePage + 1) * 50)
                                .map((trade, index) => (
                                  <tr key={index}>
                                    <td>{trade.date}</td>
                                    <td>{trade.side}</td>
                                    <td>{trade.quantity}</td>
                                    <td>
                                      {amount(trade.price, resultCurrency)}
                                    </td>
                                    <td>
                                      {amount(trade.cost, resultCurrency)}
                                    </td>
                                  </tr>
                                ))}
                            </tbody>
                          </table>
                        </div>
                        <div className="bt-pagination">
                          <button
                            disabled={!tradePage}
                            onClick={() => setTradePage(tradePage - 1)}
                          >
                            Previous trades
                          </button>
                          <span>
                            Page {tradePage + 1} of{" "}
                            {Math.ceil(result.trades.length / 50)}
                          </span>
                          <button
                            disabled={
                              (tradePage + 1) * 50 >= result.trades.length
                            }
                            onClick={() => setTradePage(tradePage + 1)}
                          >
                            Next trades
                          </button>
                        </div>
                      </>
                    )}
                  </>
                )}
                {tab === "Raw JSON" && (
                  <pre className="bt-json">
                    {JSON.stringify(completed, null, 2)}
                  </pre>
                )}
              </>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}
