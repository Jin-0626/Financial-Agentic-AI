import { price, type Snapshot } from "./bridge";
import { useResource } from "./resources";
import { DataInfo } from "./data-info";
import { useDesktopView } from "./view-settings";
export const indices = [
  { id: "yahoo:^KLSE", name: "Bursa KLCI" },
  { id: "yahoo:^GSPC", name: "S&P 500" },
  { id: "yahoo:^VIX", name: "VIX" },
];
function IndexQuote({
  id,
  name,
  onSelect,
}: {
  id: string;
  name: string;
  onSelect: (id: string) => void;
}) {
  const { payload, error } = useResource(
      { type: "quote", instrument: id },
      true,
      `ticker:${id}`,
    ),
    market = payload?.type === "quote" ? payload.market : null,
    q = market?.data;
  return (
    <button
      onClick={() => onSelect(id)}
      title={`${market?.source ?? "Source unavailable"} · ${market?.observed_at ?? "Observation unavailable"}${error ? ` · ${error}` : ""}`}
    >
      <b>{name}</b>
      <span>{price(q?.price)}</span>
      <span
        className={
          q?.change_percent == null
            ? ""
            : q.change_percent.startsWith("-")
              ? "negative"
              : "positive"
        }
      >
        {q?.change_percent == null ? "—" : `${price(q.change_percent)}%`}
      </span>
      {market?.stale && <small>STALE</small>}
      {market?.status === "unavailable" && <small>Unavailable</small>}
    </button>
  );
}
export function MarketTicker({
  snapshot,
  onSelect,
}: {
  snapshot: Snapshot;
  onSelect: (id: string) => void;
}) {
  const { view, update } = useDesktopView();
  return (
    <div className="market-ticker">
      <select
        aria-label="Ticker mode"
        value={view.ticker_mode}
        onChange={(e) =>
          update({ ticker_mode: e.target.value as typeof view.ticker_mode })
        }
      >
        <option value="indices">Indices</option>
        <option value="watchlist">Watchlist</option>
      </select>
      <div
        className="ticker-strip"
        aria-label={
          view.ticker_mode === "indices" ? "Market indices" : "Watchlist quotes"
        }
      >
        {(view.ticker_mode === "indices"
          ? indices
          : snapshot.watchlist.symbols.map((id) => ({
              id,
              name: id.split(":").slice(1).join(":"),
            }))
        ).map((index) => (
          <IndexQuote key={index.id} {...index} onSelect={onSelect} />
        ))}
        {view.ticker_mode === "watchlist" &&
          !snapshot.watchlist.symbols.length && (
            <span className="ticker-empty">No active quote subscriptions</span>
          )}
      </div>
    </div>
  );
}
function IndexCard({
  id,
  name,
  onSelect,
}: {
  id: string;
  name: string;
  onSelect: (id: string) => void;
}) {
  const quote = useResource(
      { type: "quote", instrument: id },
      true,
      `overview:${id}:quote`,
    ),
    history = useResource(
      { type: "history", instrument: id, range: "6M" },
      true,
      `overview:${id}:history`,
    );
  const market = quote.payload?.type === "quote" ? quote.payload.market : null,
    q = market?.data,
    bars =
      history.payload?.type === "history"
        ? (history.payload.market.data ?? [])
        : [];
  const samples = bars.filter(
      (_, i) => i % Math.max(1, Math.ceil(bars.length / 80)) === 0,
    ),
    min = Math.min(...samples.map((b) => b.close)),
    max = Math.max(...samples.map((b) => b.close)),
    span = Math.max(max - min, 0.00000001);
  return (
    <article className="index-card">
      <button onClick={() => onSelect(id)}>
        <span>{name}</span>
        <strong
          className={
            q?.change_percent?.startsWith("-") ? "negative" : "positive"
          }
        >
          {price(q?.price)}
        </strong>
        <small>
          {q?.change_percent == null
            ? "Change unavailable"
            : `${price(q.change_percent)}%`}
          {market?.stale ? " · STALE" : ""}
        </small>
      </button>
      {samples.length > 1 ? (
        <svg
          className={
            q?.change_percent == null
              ? ""
              : q.change_percent.startsWith("-")
                ? "negative"
                : "positive"
          }
          viewBox="0 0 180 55"
          role="img"
          aria-label={`${name} six-month history`}
        >
          <polyline
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            points={samples
              .map(
                (bar, i) =>
                  `${(i / (samples.length - 1)) * 180},${50 - ((bar.close - min) / span) * 45}`,
              )
              .join(" ")}
          />
        </svg>
      ) : (
        <small>History unavailable</small>
      )}
      {market && <DataInfo market={market} />}
    </article>
  );
}
export function IndexCards({ onSelect }: { onSelect: (id: string) => void }) {
  return (
    <div className="index-cards">
      {indices.map((index) => (
        <IndexCard key={index.id} {...index} onSelect={onSelect} />
      ))}
    </div>
  );
}
