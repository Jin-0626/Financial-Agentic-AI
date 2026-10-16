import { usePortfolioWorkspace } from "./use-workspace";
import { usePortfolioEditor } from "./use-editor";
import { PortfolioDialog } from "./dialog";
import { HoldingsTable } from "./holdings-table";
import { HistoryView } from "./history-view";
import { TransactionsView } from "./transactions-view";
import { useState } from "react";
import { PerformanceChart } from "./performance-chart";
import { amount, api, Identity, number, scope } from "../../shared/api";
export function PortfolioPage({ identity }: { identity: Identity }) {
  const workspace = usePortfolioWorkspace(identity);
  const { list, id, setId, doc, summary, error, setError, busy, refresh, act } =
    workspace;
  const editor = usePortfolioEditor(doc, setError);
  const { open, editHolding } = editor;
  const [tab, setTab] = useState("Overview");
  const [view, setView] = useState("table");
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
    <HoldingsTable
      positions={positions}
      currencyCode={currencyCode}
      doc={doc}
      busy={busy}
      editHolding={editHolding}
    />
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
                    <PerformanceChart chart={chart} />
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
            <TransactionsView
              doc={doc}
              summary={summary}
              currencyCode={currencyCode}
            />
          )}
          {tab === "History" && (
            <HistoryView
              doc={doc}
              summary={summary}
              currencyCode={currencyCode}
            />
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

      <PortfolioDialog
        identity={identity}
        workspace={workspace}
        editor={editor}
      />
    </main>
  );
}
