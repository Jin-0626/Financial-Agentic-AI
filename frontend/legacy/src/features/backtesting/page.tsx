import { useEffect, useRef, useState } from "react";

import { api, Identity, scope, send } from "../../shared/api";

import { Row, Strategy, Catalog, Completed, Result } from "./types";
import { BacktestResults, tabs } from "./results";
import { parseCSV, download } from "./data";
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
      const response = await api<Result>(
        `/backtesting/run?${scope(identity)}`,
        {
          ...send(inputs),
          signal: abort.signal,
        },
      );
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
              : completed
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
        <BacktestResults
          completed={completed}
          execution={catalog?.execution || ""}
          busy={!!busy}
          currency={currency}
          tab={tab}
          setTab={setTab}
          tradePage={tradePage}
          setTradePage={setTradePage}
        />
      </div>
    </main>
  );
}
