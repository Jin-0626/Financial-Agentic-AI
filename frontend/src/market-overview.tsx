import { StockLabel } from "./stock-label";
import { useState } from "react";
import { mutateWatchlist, price, type Snapshot } from "./bridge";
import { IndexCards } from "./index-market";
import { useResource } from "./resources";
import { DataInfo } from "./data-info";
import { OverviewCrypto, OverviewMacro } from "./overview-assets";
function MarketGroup({
  market,
  sort,
  heading,
  snapshot,
  onSelect,
}: {
  market: "MY" | "US";
  sort: "change_percent" | "volume";
  heading: string;
  snapshot: Snapshot;
  onSelect: (id: string) => void;
}) {
  const { payload, error, loading } = useResource(
      {
        type: "screener",
        market,
        sector: null,
        sort,
        descending: true,
        offset: 0,
      },
      snapshot.mode === "HTTP",
      `overview:${market}:${sort}`,
    ),
    [writeError, setError] = useState<string | null>(null),
    [pending, setPending] = useState(false);
  const envelope = payload?.type === "screener" ? payload.market : null,
    rows = envelope?.data?.slice(0, 8) ?? [];
  return (
    <section className="market-overview-group">
      <header className="market-group-heading">
        <h3>{heading}</h3>
        <span>{market}</span>
      </header>
      <p className="coverage-note">
        {market === "MY"
          ? "Ranked across retrieved MY equities · provider coverage"
          : "Broad US provider universe · ranked by returned quote values"}
      </p>
      {envelope && <DataInfo market={envelope} />}{" "}
      {(error || envelope?.error || writeError) && (
        <p role="alert">{writeError ?? error ?? envelope?.error}</p>
      )}
      <div className="resource-table">
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Last</th>
              <th>Change %</th>
              <th>Volume</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.instrument.id}>
                <td>
                  <button
                    className="instrument-button"
                    onClick={() => onSelect(row.instrument.id)}
                  >
                    <StockLabel
                      code={row.instrument.symbol}
                      name={row.instrument.name}
                    />
                  </button>
                </td>
                <td>{price(row.price)}</td>
                <td
                  className={
                    row.change_percent == null
                      ? ""
                      : row.change_percent < 0
                        ? "negative"
                        : "positive"
                  }
                >
                  {row.change_percent == null
                    ? "—"
                    : `${price(row.change_percent)}%`}
                </td>
                <td>{price(row.volume, 0)}</td>
                <td>
                  <button
                    aria-label={`Add ${row.instrument.symbol} to active watchlist`}
                    disabled={
                      pending ||
                      snapshot.watchlist.symbols.includes(row.instrument.id)
                    }
                    onClick={() => {
                      setPending(true);
                      setError(null);
                      void mutateWatchlist({
                        type: "add",
                        id: snapshot.preferences.active_watchlist_id ?? "main",
                        symbols: [row.instrument.id],
                      })
                        .catch((e) => setError(String(e)))
                        .finally(() => setPending(false));
                    }}
                  >
                    +
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!rows.length && (
          <div className="empty">
            {snapshot.mode !== "HTTP"
              ? "Market leaders require live HTTP mode."
              : loading
                ? "Loading provider results…"
                : error || envelope?.status === "unavailable"
                  ? "Provider results unavailable. Automatic retries respect provider limits."
                  : "No provider results returned"}
          </div>
        )}
      </div>
    </section>
  );
}
export function MarketOverview({
  snapshot,
  onSelect,
}: {
  snapshot: Snapshot;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="market-overview">
      <h3 className="overview-section-title">Market indices</h3>
      <IndexCards onSelect={onSelect} />
      <div className="overview-assets-grid">
        <OverviewCrypto
          enabled={snapshot.mode === "HTTP"}
          onSelect={onSelect}
        />
        <OverviewMacro enabled={snapshot.mode === "HTTP"} onSelect={onSelect} />
      </div>
      <div className="market-overview-grid">
        <MarketGroup
          market="MY"
          sort="change_percent"
          heading="Malaysia · change leaders"
          snapshot={snapshot}
          onSelect={onSelect}
        />
        <MarketGroup
          market="US"
          sort="change_percent"
          heading="United States · change leaders"
          snapshot={snapshot}
          onSelect={onSelect}
        />
        <MarketGroup
          market="MY"
          sort="volume"
          heading="Malaysia · volume leaders"
          snapshot={snapshot}
          onSelect={onSelect}
        />
        <MarketGroup
          market="US"
          sort="volume"
          heading="United States · volume leaders"
          snapshot={snapshot}
          onSelect={onSelect}
        />
      </div>
    </div>
  );
}
