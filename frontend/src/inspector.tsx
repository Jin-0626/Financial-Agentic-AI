import { StockLabel } from "./stock-label";
import { useState } from "react";
import { numeric, price, type Snapshot } from "./bridge";
import { MarketResource } from "./resources";
export function Inspector({
  snapshot,
  onClose,
  tab,
  onTab,
}: {
  snapshot: Snapshot;
  onClose: () => void;
  tab: "quote" | "news" | "calendar";
  onTab: (tab: "quote" | "news" | "calendar") => void;
}) {
  const selected = snapshot.preferences.selected,
    market = selected ? snapshot.quotes[selected] : null,
    quote = market?.data;
  const change = numeric(quote?.change_percent);
  return (
    <aside className="instrument-inspector" aria-label="Instrument inspector">
      <header>
        <h3>
          {selected ? (
            <StockLabel code={selected} name={quote?.instrument.name} />
          ) : (
            "No instrument"
          )}
        </h3>
        <button
          className="inspector-close"
          aria-label="Close inspector"
          onClick={onClose}
        >
          ×
        </button>
      </header>
      <div className="inspector-price">
        <strong>{price(quote?.price)}</strong>
        <span>{quote?.currency ?? "—"}</span>
        <small
          className={
            change == null ? "" : change >= 0 ? "positive" : "negative"
          }
        >
          {change == null ? "—" : `${price(change)}%`}
        </small>
        <span className={market?.stale ? "negative" : "tiny-label"}>
          {market?.status.toUpperCase() ?? "LOADING"}
        </span>
      </div>
      <div className="inspector-tabs">
        <button aria-pressed={tab === "quote"} onClick={() => onTab("quote")}>
          Overview
        </button>
        <button aria-pressed={tab === "news"} onClick={() => onTab("news")}>
          News
        </button>
        <button
          aria-pressed={tab === "calendar"}
          onClick={() => onTab("calendar")}
        >
          Calendar
        </button>
      </div>
      {tab === "calendar" ? (
        snapshot.mode === "HTTP" ? (
          <MarketResource request={{ type: "calendar" }} onSelect={() => {}} />
        ) : (
          <div className="empty">Calendar requires live HTTP mode.</div>
        )
      ) : tab === "quote" ? (
        <section className="instrument-details" aria-label="Instrument details">
          <h3>{quote?.instrument.name ?? "Instrument details"}</h3>
          <dl>
            {[
              ["ASSET", quote?.instrument.asset_class ?? "—"],
              ["EXCHANGE", quote?.instrument.exchange ?? "—"],
              ["CURRENCY", quote?.currency ?? "—"],
              ["BID", price(quote?.bid)],
              ["ASK", price(quote?.ask)],
              ["VOLUME", price(quote?.volume, 0)],
              ["SOURCE", market?.source ?? "—"],
            ].map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
          <p>
            {market?.observed_at
              ? `Observed ${market.observed_at}`
              : "Observation time not supplied"}
          </p>
          {market?.error && <p className="negative">{market.error}</p>}
          {market?.limitations.map((line) => (
            <p key={line}>{line}</p>
          ))}
        </section>
      ) : selected && snapshot.mode === "HTTP" ? (
        <MarketResource
          request={{ type: "news", instrument: selected }}
          onSelect={() => {}}
        />
      ) : (
        <div className="empty">
          Symbol news requires live HTTP mode and a selected instrument.
        </div>
      )}
    </aside>
  );
}
