import { price } from "./bridge";
import { DataInfo } from "./data-info";
import { useResource } from "./resources";

export function OverviewCrypto({
  enabled,
  onSelect,
}: {
  enabled: boolean;
  onSelect: (id: string) => void;
}) {
  const { payload, error, loading } = useResource(
    { type: "crypto" },
    enabled,
    "overview:crypto-cards",
  );
  const market = payload?.type === "crypto" ? payload.market : null;
  return (
    <section className="overview-asset-section" aria-label="Crypto overview">
      <h3>Crypto</h3>
      <p className="coverage-note">
        Returned provider assets · not an exchange-wide ranking
      </p>
      {market && <DataInfo market={market} />}
      {error && <p role="alert">{error}</p>}
      <div className="overview-quote-cards">
        {market?.data?.slice(0, 3).map((row) => {
          const values = row.sparkline ?? [];
          const step = Math.max(1, Math.ceil(values.length / 80));
          const samples = values.filter(
            (value, index) => Number.isFinite(value) && index % step === 0,
          );
          const min = Math.min(...samples),
            max = Math.max(...samples),
            span = Math.max(max - min, 1e-9);
          const tone =
            row.change_percent == null
              ? ""
              : row.change_percent < 0
                ? "negative"
                : "positive";
          return (
            <article className="overview-quote-card" key={row.instrument.id}>
              <button
                onClick={() => onSelect(row.instrument.id)}
                aria-label={`Open ${row.instrument.symbol} from overview`}
              >
                <b>{row.instrument.name ?? row.instrument.symbol}</b>
                <strong>{price(row.price)}</strong>
                <span className={tone}>
                  {row.change_percent == null
                    ? "Change unavailable"
                    : `${price(row.change_percent)}%`}
                </span>
                {market.stale && <small className="negative">STALE</small>}
              </button>
              {samples.length > 1 ? (
                <svg
                  className={tone}
                  viewBox="0 0 100 36"
                  role="img"
                  aria-label={`${row.instrument.symbol} provider sparkline`}
                >
                  <polyline
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.3"
                    points={samples
                      .map(
                        (v, i) =>
                          `${(i / (samples.length - 1)) * 100},${33 - ((v - min) / span) * 30}`,
                      )
                      .join(" ")}
                  />
                </svg>
              ) : (
                <small>History unavailable</small>
              )}
            </article>
          );
        })}
      </div>
      {!market?.data?.length && (
        <p className="overview-empty">
          {loading ? "Loading crypto…" : "Crypto unavailable"}
        </p>
      )}
    </section>
  );
}

export function OverviewMacro({
  enabled,
  onSelect,
}: {
  enabled: boolean;
  onSelect: (id: string) => void;
}) {
  const { payload, error, loading } = useResource(
    { type: "macro" },
    enabled,
    "overview:macro-cards",
  );
  const market = payload?.type === "macro" ? payload.market : null;
  const rows = market?.data ?? [];
  const groups = [
    {
      name: "Commodity proxies",
      note: "ETF proxies, not spot commodity prices",
      rows: rows.filter((row) => row.kind === "ETF proxy"),
    },
    {
      name: "Rates",
      note: "Dated official observations, not live bond quotes",
      rows: rows.filter(
        (row) => row.kind === "yield" || row.kind === "policy rate",
      ),
    },
  ];
  return (
    <section
      className="overview-asset-section overview-macro"
      aria-label="Macro overview"
    >
      {groups.map((group) => (
        <section key={group.name}>
          <h3>{group.name}</h3>
          <p className="coverage-note">{group.note}</p>
          <div className="overview-quote-cards">
            {group.rows.slice(0, 3).map((row, index) => (
              <article
                className="overview-quote-card macro-card"
                key={`${row.name}:${index}`}
              >
                {row.instrument ? (
                  <button
                    onClick={() => onSelect(row.instrument!.id)}
                    aria-label={`Open ${row.name} from overview`}
                  >
                    <b>{row.name}</b>
                  </button>
                ) : (
                  <b>{row.name}</b>
                )}
                <strong>
                  {price(row.price)}
                  {row.unit === "percent" ? "%" : ""}
                </strong>
                {row.change_percent != null && (
                  <span
                    className={
                      row.change_percent.startsWith("-")
                        ? "negative"
                        : "positive"
                    }
                  >
                    {price(row.change_percent)}%
                  </span>
                )}
                {row.currency && <small>{row.currency}</small>}
                <small>{row.source ?? "Source unavailable"}</small>
                <time>{row.observed_at ?? "Observation unavailable"}</time>
                {market?.stale && <small className="negative">STALE</small>}
              </article>
            ))}
          </div>
          {!group.rows.length && (
            <p className="overview-empty">
              {loading ? "Loading observations…" : "No observations returned"}
            </p>
          )}
        </section>
      ))}
      {market && <DataInfo market={market} />}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
