import { useEffect, useState } from "react";
import { useDesktopView } from "./view-settings";
import { DataInfo } from "./data-info";
import { MarketOverview } from "./market-overview";
import { mutateWatchlist, namedLists, price, type Snapshot } from "./bridge";
import { useResource, type ResourceRequest, type Asset } from "./resources";
import {
  useBrowserSettings,
  BrowserSaveStatus,
  PresetControls,
  ColumnControls,
} from "./browser-settings";
import {
  marketKey,
  marketLabels,
  type MarketColumn,
  type MarketSettings,
  type MarketSort,
} from "./browser-model";
import { compareExact } from "./decimal";
export function sortAssets(
  rows: Asset[],
  sort: MarketSort,
  descending: boolean,
): Asset[] {
  const value = (row: Asset): string | number | null =>
    sort === "symbol" ? row.instrument.symbol : (row[sort] ?? null);
  return [...rows].sort((a, b) => {
    const x = value(a),
      y = value(b);
    if (x === null) return y === null ? 0 : 1;
    if (y === null) return -1;
    const result =
      sort === "price"
        ? compareExact(String(x), String(y))
        : typeof x === "number" && typeof y === "number"
          ? x - y
          : String(x).localeCompare(String(y));
    return (
      (descending ? -1 : 1) * result ||
      a.instrument.id.localeCompare(b.instrument.id)
    );
  });
}
export function MarketsBrowser({
  snapshot,
  onSelect,
}: {
  snapshot: Snapshot;
  onSelect: (id: string) => void;
}) {
  const { view, update } = useDesktopView();
  return (
    <section
      className="feature-screen markets-browser"
      aria-label="Market browser"
    >
      <div className="market-tabs-row">
        <div
          className="market-tabs"
          role="tablist"
          aria-label="Market universe"
        >
          {(["overview", "MY", "US", "crypto"] as const).map((value) => (
            <button
              role="tab"
              aria-selected={view.market_tab === value}
              key={value}
              onClick={() => update({ market_tab: value })}
            >
              {value === "overview"
                ? "Overview"
                : value === "crypto"
                  ? "Crypto"
                  : `${value} Equities`}
            </button>
          ))}
        </div>
        <span className="market-add-target">
          Add to:{" "}
          {namedLists(snapshot).find(
            (l) =>
              l.id === (snapshot.preferences.active_watchlist_id ?? "main"),
          )?.name ?? "My Watchlist"}
        </span>
      </div>
      <p className="coverage-note">
        Provider coverage; this is not an exhaustive exchange listing.
      </p>
      {view.market_tab === "overview" ? (
        <MarketOverview snapshot={snapshot} onSelect={onSelect} />
      ) : (
        <MarketTable
          key={view.market_tab}
          market={view.market_tab}
          snapshot={snapshot}
          onSelect={onSelect}
        />
      )}
    </section>
  );
}
export function ScreenerBrowser({
  snapshot,
  onSelect,
}: {
  snapshot: Snapshot;
  onSelect: (id: string) => void;
}) {
  const [market, setMarket] = useState<"MY" | "US">("MY");
  const { preferences, mutate, pending, error } = useBrowserSettings();
  return (
    <section className="screener-workspace" aria-label="Equity screener">
      <aside className="screener-presets" aria-label="Saved screeners">
        <h2>My Screeners</h2>
        <p>Local views · shared with Markets</p>
        {!preferences.market_presets.length && (
          <p className="empty">
            Save filters using Presets to create a screener.
          </p>
        )}
        {preferences.market_presets.map((preset) => (
          <button
            key={preset.name}
            disabled={pending || !!error}
            onClick={() =>
              mutate({
                type: "market_preset",
                market,
                action: { type: "apply", name: preset.name },
              })
            }
            title={`Apply ${preset.name} to ${market}`}
          >
            {preset.name}
          </button>
        ))}
      </aside>
      <div className="feature-screen screener-results">
        <div className="page-tabs" role="tablist" aria-label="Screener market">
          {(["MY", "US"] as const).map((value) => (
            <button
              key={value}
              role="tab"
              aria-selected={market === value}
              onClick={() => setMarket(value)}
            >
              {value} Equities
            </button>
          ))}
        </div>
        <p className="coverage-note">
          Provider results, not an exhaustive exchange listing. Saved views
          contain filters and columns; results refresh from the provider.
        </p>
        <MarketTable
          key={market}
          market={market}
          snapshot={snapshot}
          onSelect={onSelect}
        />
      </div>
    </section>
  );
}
function MarketTable({
  market,
  snapshot,
  onSelect,
}: {
  market: "MY" | "US" | "crypto";
  snapshot: Snapshot;
  onSelect: (id: string) => void;
}) {
  const {
      preferences,
      pending: saving,
      error: saveError,
      mutate,
    } = useBrowserSettings(),
    settings = preferences[marketKey(market)],
    disabled = saving || !!saveError;
  const [offset, setOffset] = useState(0),
    [form, setForm] = useState(settings),
    [local, setLocal] = useState(settings.filter),
    [writeError, setWriteError] = useState<string | null>(null),
    [writing, setWriting] = useState(false);
  useEffect(() => {
    setForm(settings);
    setLocal(settings.filter);
    setOffset(0);
  }, [settings]);
  const change = (patch: Partial<MarketSettings>) => {
    setOffset(0);
    mutate({ type: "market", market, settings: { ...settings, ...patch } });
  };
  const request: ResourceRequest =
    market === "crypto"
      ? { type: "crypto" }
      : {
          type: "screener",
          market,
          sector: settings.sector,
          sort: settings.sort,
          descending: settings.descending,
          offset,
          min_cap: settings.min_cap,
          min_change: settings.min_change,
          min_volume: settings.min_volume,
        };
  const { payload, error, loading } = useResource(
    request,
    snapshot.mode === "HTTP",
    "markets:browser",
  );
  const envelope =
      payload?.type === "screener" || payload?.type === "crypto"
        ? payload.market
        : null,
    rows = envelope?.data ?? [],
    visible = (
      market === "crypto"
        ? sortAssets(rows, settings.sort, settings.descending)
        : rows
    ).filter((row) =>
      `${row.instrument.symbol} ${row.instrument.name ?? ""}`
        .toLocaleLowerCase()
        .includes(local.trim().toLocaleLowerCase()),
    ),
    active = snapshot.preferences.active_watchlist_id ?? "main",
    list = namedLists(snapshot).find((l) => l.id === active);
  const sortBy = (sort: MarketSort) =>
    change({
      sort,
      descending: sort === settings.sort ? !settings.descending : true,
    });
  const cell = (row: Asset, column: MarketColumn) => {
    switch (column) {
      case "instrument":
        return (
          <button
            className="instrument-button"
            aria-label={`Open ${row.instrument.symbol}`}
            onClick={() => onSelect(row.instrument.id)}
          >
            <strong>{row.instrument.symbol}</strong>
            <small>{row.instrument.name ?? "Name unavailable"}</small>
          </button>
        );
      case "name":
        return row.instrument.name ?? "Unavailable";
      case "price":
        return price(row.price);
      case "change_percent":
        return (
          <span
            className={
              row.change_percent == null
                ? ""
                : row.change_percent < 0
                  ? "negative"
                  : "positive"
            }
          >
            {row.change_percent == null ? "—" : `${price(row.change_percent)}%`}
          </span>
        );
      case "volume":
        return price(row.volume, 0);
      case "market_cap":
        return price(row.market_cap, 0);
      case "sector":
        return row.sector ?? "Unavailable";
      case "currency":
        return row.currency ?? "Unavailable";
      case "exchange":
        return row.instrument.exchange ?? "Unavailable";
      case "status":
        return (
          <span className={envelope?.stale ? "negative" : ""}>
            {envelope?.stale ? "STALE" : (envelope?.status ?? "Unavailable")}
          </span>
        );
    }
  };
  return (
    <>
      <div className="feature-toolbar">
        <div className="rank-views" aria-label="Equity rankings">
          {(
            [
              { label: "Market Cap", sort: "market_cap", descending: true },
              { label: "Gainers", sort: "change_percent", descending: true },
              { label: "Losers", sort: "change_percent", descending: false },
              { label: "Volume Leaders", sort: "volume", descending: true },
            ] as const
          ).map((rank) => (
            <button
              key={rank.label}
              disabled={disabled}
              aria-pressed={
                settings.sort === rank.sort &&
                settings.descending === rank.descending
              }
              onClick={() =>
                change({ sort: rank.sort, descending: rank.descending })
              }
            >
              {rank.label}
            </button>
          ))}
        </div>
        <ColumnControls
          title="Market columns"
          columns={settings.columns}
          labels={marketLabels}
          required="instrument"
          disabled={disabled}
          onSave={(columns) => change({ columns })}
        />
        <PresetControls market={market} />
      </div>
      {market !== "crypto" && (
        <form
          className="feature-toolbar market-filter-form"
          onSubmit={(event) => {
            event.preventDefault();
            change({
              sector: form.sector?.trim() || null,
              min_cap: form.min_cap || null,
              min_change: form.min_change || null,
              min_volume: form.min_volume || null,
            });
          }}
        >
          <label>
            Sector
            <input
              aria-label="Market sector"
              maxLength={80}
              disabled={disabled}
              value={form.sector ?? ""}
              onChange={(e) => setForm({ ...form, sector: e.target.value })}
            />
          </label>
          {(["min_cap", "min_change", "min_volume"] as const).map((key) => (
            <label key={key}>
              {
                {
                  min_cap: "Minimum market cap",
                  min_change: "Minimum change %",
                  min_volume: "Minimum volume",
                }[key]
              }
              <input
                aria-label={
                  {
                    min_cap: "Minimum market cap",
                    min_change: "Minimum change %",
                    min_volume: "Minimum volume",
                  }[key]
                }
                type="text"
                inputMode="decimal"
                pattern={
                  key === "min_volume"
                    ? "[0-9]+"
                    : key === "min_change"
                      ? "[+-]?[0-9]+([.][0-9]+)?"
                      : "[0-9]+([.][0-9]+)?"
                }
                maxLength={28}
                disabled={disabled}
                value={form[key] ?? ""}
                onChange={(e) => setForm({ ...form, [key]: e.target.value })}
              />
            </label>
          ))}
          <button disabled={disabled}>Apply sector</button>
        </form>
      )}
      <div className="feature-toolbar">
        <label>
          Sort
          <select
            aria-label="Market sort"
            value={settings.sort}
            disabled={disabled}
            onChange={(e) => change({ sort: e.target.value as MarketSort })}
          >
            {(
              [
                "market_cap",
                "change_percent",
                "volume",
                "symbol",
                "price",
              ] as const
            ).map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
        <button
          disabled={disabled}
          onClick={() => change({ descending: !settings.descending })}
        >
          {settings.descending ? "Descending" : "Ascending"}
        </button>
        <label>
          Filter loaded results
          <input
            aria-label="Filter loaded market results"
            maxLength={80}
            value={local}
            disabled={disabled}
            onChange={(e) => setLocal(e.target.value)}
            onBlur={() => {
              if (local !== settings.filter) change({ filter: local });
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                change({ filter: local });
              }
            }}
          />
        </label>
        <span>
          {visible.length}/{rows.length} loaded rows ·{" "}
          {market === "crypto"
            ? "Sorting applies to returned results"
            : "Ranking and numeric filters are provider-side"}
        </span>
      </div>
      <BrowserSaveStatus />
      {snapshot.mode !== "HTTP" && (
        <div className="empty">Market browsing requires live HTTP mode.</div>
      )}
      {(error || writeError) && (
        <p role="alert" className="negative">
          {error ?? writeError}
        </p>
      )}
      {loading && snapshot.mode === "HTTP" && (
        <p role="status">Loading provider market data…</p>
      )}
      {envelope && <DataInfo market={envelope} />}
      <div className="resource-table market-custom-table">
        <table>
          <thead>
            <tr>
              {settings.columns.map((column) => {
                const sort = column === "instrument" ? "symbol" : column;
                const sortable = [
                  "symbol",
                  "price",
                  "change_percent",
                  "market_cap",
                  "volume",
                ].includes(sort);
                return (
                  <th
                    key={column}
                    aria-sort={
                      sortable && settings.sort === sort
                        ? settings.descending
                          ? "descending"
                          : "ascending"
                        : "none"
                    }
                  >
                    {sortable ? (
                      <button
                        disabled={disabled}
                        onClick={() => sortBy(sort as MarketSort)}
                      >
                        {marketLabels[column]}
                      </button>
                    ) : (
                      marketLabels[column]
                    )}
                  </th>
                );
              })}
              <th>Watchlist</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => (
              <tr
                key={row.instrument.id}
                className={
                  snapshot.preferences.selected === row.instrument.id
                    ? "active-instrument"
                    : undefined
                }
              >
                {settings.columns.map((column) => (
                  <td key={column}>{cell(row, column)}</td>
                ))}
                <td>
                  <button
                    disabled={
                      writing || !!list?.symbols.includes(row.instrument.id)
                    }
                    aria-label={`Add ${row.instrument.symbol} to watchlist`}
                    onClick={() => {
                      setWriteError(null);
                      setWriting(true);
                      void mutateWatchlist({
                        type: "add",
                        id: active,
                        symbols: [row.instrument.id],
                      })
                        .catch((e) => setWriteError(String(e)))
                        .finally(() => setWriting(false));
                    }}
                  >
                    {list?.symbols.includes(row.instrument.id)
                      ? "Added"
                      : "+ Add"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!visible.length && !loading && (
          <div className="empty">
            {rows.length
              ? "No loaded instruments match this local filter."
              : "No instruments returned for this market and filter."}
          </div>
        )}
      </div>
      {market !== "crypto" && (
        <div className="table-pagination">
          <button
            disabled={!offset || loading}
            onClick={() => setOffset((value) => Math.max(0, value - 100))}
          >
            Previous market page
          </button>
          <span>
            {rows.length ? `${offset + 1}–${offset + rows.length}` : "No rows"}{" "}
            · provider results
          </span>
          <button
            disabled={rows.length < 100 || offset >= 20000 || loading}
            onClick={() => setOffset((value) => value + 100)}
          >
            Next market page
          </button>
        </div>
      )}
    </>
  );
}
