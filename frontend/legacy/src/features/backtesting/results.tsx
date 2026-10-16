import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { amount } from "../../shared/api";
import { Completed } from "./types";
import { download } from "./data";
export const tabs = [
  "Summary",
  "Equity Curve",
  "Metrics",
  "Trades",
  "Raw JSON",
] as const;
const percent = (n: number) => `${(n * 100).toFixed(2)}%`;

type Props = {
  execution: string;
  completed: Completed | null;
  busy: boolean;
  currency: string;
  tab: (typeof tabs)[number];
  setTab: (value: (typeof tabs)[number]) => void;
  tradePage: number;
  setTradePage: (value: number) => void;
};
export function BacktestResults({
  completed,
  execution,
  busy,
  currency,
  tab,
  setTab,
  tradePage,
  setTradePage,
}: Props) {
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
              Load daily history or import closing prices, choose a strategy,
              and run.
            </p>
            <p>{execution}</p>
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
                  End equity includes any open position. Returns describe this
                  price series; they are not a forecast.
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
                            {["Date", "Side", "Quantity", "Price", "Cost"].map(
                              (name) => (
                                <th key={name}>{name}</th>
                              ),
                            )}
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
                                <td>{amount(trade.price, resultCurrency)}</td>
                                <td>{amount(trade.cost, resultCurrency)}</td>
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
                        disabled={(tradePage + 1) * 50 >= result.trades.length}
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
  );
}
