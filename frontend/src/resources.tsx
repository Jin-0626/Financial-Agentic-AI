import { StockLabel } from "./stock-label";
import { OptionsScreen } from "./options-screen";
import { CalendarScreen } from "./calendar-screen";
import { NewsScreen } from "./news-screen";
import { DataInfo } from "./data-info";
import { DataTable as Table } from "./shared-ui";
import { useSubscription } from "./subscriptions";
import { useEffect, useState, type ReactNode } from "react";
import { price, type Instrument, type Market, type Screen } from "./bridge";
import {
  PortfolioScreen,
  ResearchHistoryScreen,
  TracesScreen,
  BacktestingScreen,
} from "./services";

export type ResourceRequest =
  | { type: "quote"; instrument: string }
  | { type: "history"; instrument: string; range: import("./bridge").Range }
  | { type: "capabilities" | "crypto" | "macro" | "calendar" }
  | { type: "search"; query: string }
  | { type: "news"; instrument: string | null }
  | { type: "earnings"; instrument: string }
  | { type: "options"; instrument: string; expiry: string | null }
  | {
      type: "screener";
      market: "MY" | "US";
      sector: string | null;
      sort: "market_cap" | "change_percent" | "volume" | "symbol" | "price";
      descending: boolean;
      offset: number;
      min_cap?: string | null;
      min_change?: string | null;
      min_volume?: string | null;
    };
export type Article = {
  title: string;
  url: string;
  publisher: string | null;
  published_at: string | null;
};
export type Asset = {
  instrument: Instrument;
  price: string | null;
  market_cap: number | null;
  change_percent: number | null;
  sector: string | null;
  volume: number | null;
  sparkline: number[];
  currency?: string | null;
};
type Macro = {
  name: string;
  kind: string;
  price: string | null;
  unit: string | null;
  source: string | null;
  observed_at: string | null;
  instrument?: Instrument | null;
  change_percent?: string | null;
  currency?: string | null;
};
export type Calendar = {
  title: string | null;
  country: string | null;
  date: string | null;
  impact: string | null;
  forecast: string | null;
  previous: string | null;
  actual: string | null;
  actual_status: string | null;
};
type Earnings = {
  date: string;
  eps_estimate: number | null;
  eps_actual: number | null;
  surprise_percent: number | null;
  next_session_move_percent: number | null;
  move_status: string;
};
export type Contract = {
  contract: string;
  strike: string | null;
  bid: string | null;
  ask: string | null;
  last: string | null;
  volume: string | null;
  open_interest: string | null;
  implied_volatility: string | null;
  itm: boolean;
  observed_at?: string | null;
};
export type Chain = {
  calls: Contract[];
  puts: Contract[];
  expiries: string[];
  expiry: string;
};
export type Capabilities = {
  service: string;
  features: Record<string, boolean>;
  widgets: string[];
  coverage: string;
};
export type Payload =
  | { type: "quote"; market: Market<import("./bridge").Quote> }
  | { type: "history"; market: Market<import("./bridge").Candle[]> }
  | { type: "capabilities"; market: Capabilities }
  | { type: "search"; market: Market<Instrument[]> }
  | { type: "news"; market: Market<Article[]> }
  | { type: "crypto" | "screener"; market: Market<Asset[]> }
  | { type: "macro"; market: Market<Macro[]> }
  | { type: "calendar"; market: Market<Calendar[]> }
  | { type: "earnings"; market: Market<Earnings[]> }
  | { type: "options"; market: Market<Chain> };
export const useResource = useSubscription;

export function MarketResource({
  request,
  onSelect,
  owner,
}: {
  request: ResourceRequest;
  onSelect: (id: string) => void;
  owner?: string;
}) {
  const { payload, error, refresh } = useResource(request, true, owner);
  let content: ReactNode = null;
  if (payload)
    switch (payload.type) {
      case "quote":
      case "history":
        content = <p>Use a chart panel for quote and history data.</p>;
        break;
      case "capabilities":
        content = (
          <p>
            {payload.market.service}: {payload.market.coverage}
          </p>
        );
        break;
      case "search":
        content = (
          <Table
            columns={["Instrument", "Name", "Exchange", "Asset"]}
            rows={(payload.market.data ?? []).map((row) => [
              <button onClick={() => onSelect(row.id)}>
                <StockLabel code={row.id} name={row.name} />
              </button>,
              row.name,
              row.exchange,
              row.asset_class,
            ])}
          />
        );
        break;
      case "news":
        content = (
          <div className="news-list">
            {(payload.market.data ?? []).map((row) => (
              <article key={row.url}>
                <a
                  href={/^https?:\/\//i.test(row.url) ? row.url : undefined}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {row.title}
                </a>
                <p>
                  {row.publisher ?? "Publisher not supplied"} ·{" "}
                  {row.published_at ?? "Date not supplied"}
                </p>
              </article>
            ))}
          </div>
        );
        break;
      case "crypto":
      case "screener":
        content = (
          <>
            <Table
              columns={[
                "Instrument",
                "Price",
                "Change %",
                "Market cap",
                "Sector",
                "Volume",
              ]}
              rows={(payload.market.data ?? []).map((row) => [
                <button onClick={() => onSelect(row.instrument.id)}>
                  <StockLabel
                    code={row.instrument.symbol}
                    name={row.instrument.name}
                  />
                </button>,
                price(row.price),
                <span
                  className={
                    (row.change_percent ?? 0) >= 0 ? "positive" : "negative"
                  }
                >
                  {price(row.change_percent)}%
                </span>,
                price(row.market_cap, 0),
                row.sector,
                price(row.volume, 0),
              ])}
            />
            <div className="heatmap" aria-label="Market change heatmap">
              {(payload.market.data ?? []).map((row) => (
                <button
                  key={row.instrument.id}
                  onClick={() => onSelect(row.instrument.id)}
                  className={
                    (row.change_percent ?? 0) >= 0 ? "gain-cell" : "loss-cell"
                  }
                >
                  <StockLabel
                    code={row.instrument.symbol}
                    name={row.instrument.name}
                  />
                  <strong>{price(row.change_percent)}%</strong>
                </button>
              ))}
            </div>
          </>
        );
        break;
      case "macro":
        content = (
          <Table
            columns={["Series", "Type", "Value", "Unit", "Observed", "Source"]}
            rows={(payload.market.data ?? []).map((row) => [
              row.instrument ? (
                <StockLabel
                  code={row.instrument.symbol}
                  name={row.instrument.name ?? row.name}
                />
              ) : (
                row.name
              ),
              row.kind,
              price(row.price),
              row.unit,
              row.observed_at,
              row.source,
            ])}
          />
        );
        break;
      case "calendar":
        content = (
          <Table
            columns={[
              "Release",
              "Country",
              "Date",
              "Impact",
              "Forecast",
              "Previous",
              "Actual",
              "Evidence",
            ]}
            rows={(payload.market.data ?? []).map((row) => [
              row.title,
              row.country,
              row.date,
              row.impact,
              row.forecast,
              row.previous,
              row.actual,
              row.actual_status,
            ])}
          />
        );
        break;
      case "earnings":
        content = (
          <Table
            columns={[
              "Date",
              "EPS estimate",
              "EPS actual",
              "Surprise %",
              "Next session %",
              "Coverage",
            ]}
            rows={(payload.market.data ?? []).map((row) => [
              row.date,
              price(row.eps_estimate),
              price(row.eps_actual),
              price(row.surprise_percent),
              price(row.next_session_move_percent),
              row.move_status,
            ])}
          />
        );
        break;
      case "options":
        content = payload.market.data ? (
          <>
            {["calls", "puts"].map((side) => (
              <section key={side}>
                <h3>
                  {side.toUpperCase()} · {payload.market.data?.expiry}
                </h3>
                <Table
                  columns={[
                    "Contract",
                    "Strike",
                    "Bid",
                    "Ask",
                    "Last",
                    "Volume",
                    "Open interest",
                    "IV",
                  ]}
                  rows={(side === "calls"
                    ? (payload.market.data?.calls ?? [])
                    : (payload.market.data?.puts ?? [])
                  ).map((row) => [
                    row.contract,
                    price(row.strike),
                    price(row.bid),
                    price(row.ask),
                    price(row.last),
                    price(row.volume, 0),
                    price(row.open_interest, 0),
                    price(row.implied_volatility, 4),
                  ])}
                />
              </section>
            ))}
          </>
        ) : null;
        break;
    }
  const market =
    payload && payload.type !== "capabilities" ? payload.market : null;
  return (
    <section className="resource-panel">
      <header>
        <h3>{request.type.toUpperCase()}</h3>
        <button onClick={refresh}>Check for updates</button>
      </header>
      {error && (
        <p role="alert" className="negative">
          {error}
        </p>
      )}
      {!payload && !error && (
        <div className="empty">Loading live provider data…</div>
      )}
      {market && <DataInfo market={market} />}
      {content}
    </section>
  );
}
export function FeatureScreen({
  screen,
  selected,
  onSelect,
  mode,
}: {
  screen: Screen;
  selected: string | null;
  onSelect: (id: string) => void;
  mode: string;
}) {
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [market, setMarket] = useState<"MY" | "US">("MY");
  const [sector, setSector] = useState("");
  const [sort, setSort] = useState<
    "market_cap" | "change_percent" | "volume" | "symbol" | "price"
  >("market_cap");
  const [offset, setOffset] = useState(0);
  const [expiry, setExpiry] = useState("");
  const [globalNews, setGlobalNews] = useState(true);
  const capabilities = useResource({ type: "capabilities" }, mode === "HTTP");
  if (screen === "options")
    return <OptionsScreen selected={selected} mode={mode} />;
  if (screen === "calendar")
    return <CalendarScreen selected={selected} mode={mode} />;
  if (screen === "news") return <NewsScreen selected={selected} mode={mode} />;
  let request: ResourceRequest | null = null;
  switch (screen) {
    case "crypto":
      request = { type: "crypto" };
      break;
    case "macro":
      request = { type: "macro" };
      break;
    case "screener":
      request = {
        type: "screener",
        market,
        sector: sector || null,
        sort,
        descending: true,
        offset,
      };
      break;
    case "markets":
      request = search ? { type: "search", query: search } : null;
      break;
    case "dashboard":
    case "watchlist":
    case "portfolio":
    case "research":
    case "backtesting":
    case "traces":
    case "settings":
      break;
  }
  const unavailable = [
    "portfolio",
    "research",
    "backtesting",
    "traces",
  ].includes(screen);
  const backend =
    capabilities.payload?.type === "capabilities"
      ? capabilities.payload.market
      : null;
  return (
    <div className="feature-screen">
      {(screen === "markets" || screen === "screener") && (
        <div className="feature-toolbar">
          <h2>{screen.toUpperCase()}</h2>
          {selected && (
            <span>
              Linked to <StockLabel code={selected} />
            </span>
          )}
          {screen === "markets" && (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                setSearch(query.trim());
              }}
            >
              <input
                aria-label="Instrument search"
                value={query}
                maxLength={80}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search name, equity symbol or crypto asset"
              />
              <button disabled={!query.trim()}>Search</button>
            </form>
          )}
          {screen === "screener" && (
            <>
              <select
                aria-label="Equity market"
                value={market}
                onChange={(event) => {
                  setMarket(event.target.value as "MY" | "US");
                  setOffset(0);
                }}
              >
                <option>MY</option>
                <option>US</option>
              </select>
              <input
                aria-label="Sector filter"
                value={sector}
                maxLength={80}
                onChange={(event) => {
                  setSector(event.target.value);
                  setOffset(0);
                }}
                placeholder="Sector"
              />
              <select
                aria-label="Sort equities"
                value={sort}
                onChange={(event) => {
                  setSort(event.target.value as typeof sort);
                  setOffset(0);
                }}
              >
                {[
                  "market_cap",
                  "change_percent",
                  "volume",
                  "symbol",
                  "price",
                ].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
              <button
                disabled={!offset}
                onClick={() => setOffset(Math.max(0, offset - 100))}
              >
                Previous
              </button>
              <span>
                {offset + 1}–{offset + 100}
              </span>
              <button
                disabled={offset >= 49900}
                onClick={() => setOffset(offset + 100)}
              >
                Next
              </button>
            </>
          )}
        </div>
      )}
      {mode === "MOCK" ? (
        <div className="empty">
          Live screens require HTTP mode. Restart with TERMINAL_PROVIDER=http.
        </div>
      ) : request ? (
        <MarketResource
          key={JSON.stringify(request)}
          request={request}
          onSelect={onSelect}
        />
      ) : unavailable ? (
        capabilities.error ? (
          <p role="alert">{capabilities.error}</p>
        ) : !backend ? (
          <p>Discovering backend capabilities…</p>
        ) : backend.features[screen] ? (
          screen === "portfolio" ? (
            <PortfolioScreen />
          ) : screen === "research" ? (
            <ResearchHistoryScreen />
          ) : screen === "traces" ? (
            <TracesScreen />
          ) : (
            <BacktestingScreen selected={selected} />
          )
        ) : (
          <div className="empty">
            This backend ({backend.service}) does not provide {screen}.
            {screen === "research"
              ? " Research uses the desktop’s configured backend. Check its Research readiness and RESEARCH_ENABLED setting."
              : " Check the configured backend’s capabilities and service settings."}
          </div>
        )
      ) : screen === "markets" ? (
        <p>
          Search globally to retain verified exchange and crypto identifiers.
        </p>
      ) : null}
    </div>
  );
}
