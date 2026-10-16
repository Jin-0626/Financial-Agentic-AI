import type { Market } from "./bridge";
export function DataInfo({ market }: { market: Market<unknown> }) {
  return (
    <details className="data-info">
      <summary>
        <span className={`data-state ${market.status}`}>
          {market.stale ? "STALE" : market.status.toUpperCase()}
        </span>{" "}
        · {market.source ?? "Source unavailable"}
        <span className="data-time">
          {market.observed_at
            ? `Observed ${market.observed_at}`
            : "Observation unavailable"}
        </span>
      </summary>
      <div>
        <p>Fetched: {market.fetched_at ?? "Unavailable"}</p>
        <p>{market.coverage ?? "Coverage unavailable"}</p>
        {market.error && (
          <p role="alert" className="negative">
            {market.error}
          </p>
        )}
        {market.limitations.map((line) => (
          <p key={line}>{line}</p>
        ))}
      </div>
    </details>
  );
}
