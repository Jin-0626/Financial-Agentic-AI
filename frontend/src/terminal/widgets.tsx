import { useWidgetState } from "./settings";
import { useMarket, Market, display } from "./market";
import { Instrument, useTerminal } from "./state";
export type Quote = {
  instrument: Instrument;
  currency?: string | null;
  price?: string | null;
  change_percent?: string | number | null;
  [key: string]: unknown;
};
export type MarketRow = {
  instrument: Instrument;
  currency?: string | null;
  sector?: string;
  market_cap?: number | null;
  volume?: number | null;
  price?: string | null;
  change_percent?: number | null;
  sparkline?: number[];
  [key: string]: unknown;
};
export function MarketStatus({ value }: { value: Market<unknown> | null }) {
  return (
    <footer className="market-status">
      {!value ? (
        "Loading provider data…"
      ) : (
        <>
          <span className={value.status !== "ok" ? "negative" : ""}>
            {value.status === "stale"
              ? "STALE · "
              : value.status === "unavailable"
                ? "UNAVAILABLE · "
                : ""}
            {value.error || value.source || "No source supplied"}
          </span>
          <span>
            {value.observed_at
              ? `As of ${value.observed_at}`
              : "Observation time not supplied"}
          </span>
          <span>{value.coverage}</span>
          {value.limitations?.map((line, i) => (
            <span key={i}>{line}</span>
          ))}
        </>
      )}
    </footer>
  );
}
export function QuoteWidget({
  instrument,
  active,
}: {
  instrument: Instrument;
  active: boolean;
}) {
  const value = useMarket<Quote>(
    `/market/quote?instrument=${encodeURIComponent(instrument.id)}`,
    15000,
    active,
  );
  const q = value?.data;
  return (
    <>
      <div className="quote-price" key={String(q?.price)}>
        {display(q?.price, 4)} <small>{q?.currency || "—"}</small>
      </div>
      <p className={Number(q?.change_percent) >= 0 ? "positive" : "negative"}>
        {display(q?.change_percent)}%
      </p>
      <dl className="quote-fields">
        {[
          "bid",
          "ask",
          "open",
          "high",
          "low",
          "previous_close",
          "volume",
          "market_cap",
          "pe",
          "eps",
          "dividend_yield",
          "week52_low",
          "week52_high",
          "beta",
          "shares_outstanding",
        ].map((key) => (
          <div key={key}>
            <dt>{key.replaceAll("_", " ")}</dt>
            <dd>{display(q?.[key], key === "eps" ? 4 : 2)}</dd>
          </div>
        ))}
      </dl>
      <MarketStatus value={value} />
    </>
  );
}
export function WatchlistWidget({ active }: { active: boolean }) {
  const { state, update, select } = useTerminal();
  return (
    <>
      <p className="muted">Shift+Enter in symbol search adds an instrument.</p>
      {state.watchlist.map((asset) => (
        <WatchRow
          key={asset.id}
          instrument={asset}
          active={active}
          onSelect={() => select(asset)}
          onRemove={() =>
            update((old) => ({
              ...old,
              watchlist: old.watchlist.filter((i) => i.id !== asset.id),
            }))
          }
        />
      ))}
    </>
  );
}
function WatchRow({
  instrument,
  active,
  onSelect,
  onRemove,
}: {
  instrument: Instrument;
  active: boolean;
  onSelect: () => void;
  onRemove: () => void;
}) {
  const data = useMarket<Quote>(
    `/market/quote?instrument=${encodeURIComponent(instrument.id)}`,
    15000,
    active,
  );
  return (
    <div className="watch-row">
      <button onClick={onSelect}>
        {instrument.symbol}
        <small>{instrument.exchange}</small>
      </button>
      <span>
        {display(data?.data?.price)} <small>{data?.data?.currency}</small>
        <small
          className={
            Number(data?.data?.change_percent) >= 0 ? "positive" : "negative"
          }
        >
          {display(data?.data?.change_percent)}%
        </small>
      </span>
      <button
        onClick={onRemove}
        aria-label={`Remove ${instrument.symbol} from watchlist`}
      >
        ×
      </button>
    </div>
  );
}
export function NewsWidget({
  instrument,
  active,
}: {
  instrument: Instrument;
  active: boolean;
}) {
  const [global, setGlobal] = useWidgetState("globalNews", false);
  const data = useMarket<
    {
      title: string;
      url: string;
      publisher: string;
      published_at: string | null;
    }[]
  >(
    `/market/news${global ? "" : `?instrument=${encodeURIComponent(instrument.id)}`}`,
    120000,
    active,
  );
  return (
    <>
      <label>
        <input
          type="checkbox"
          checked={global}
          onChange={(e) => setGlobal(e.target.checked)}
        />{" "}
        Global news
      </label>
      {data?.data?.map((item) => (
        <article className="news-item" key={item.url}>
          <a href={item.url} target="_blank" rel="noopener noreferrer">
            {item.title}
          </a>
          <small>
            {item.publisher || "Publisher unavailable"} ·{" "}
            {item.published_at || "Publication date unknown"}
          </small>
        </article>
      ))}
      <MarketStatus value={data} />
    </>
  );
}
export function ScreenerWidget({
  active,
  onPick,
}: {
  active: boolean;
  onPick: (i: Instrument) => void;
}) {
  const [market, setMarket] = useWidgetState("market", "MY"),
    [sector, setSector] = useWidgetState("sector", ""),
    [cap, setCap] = useWidgetState("minCap", ""),
    [change, setChange] = useWidgetState("minChange", ""),
    [volume, setVolume] = useWidgetState("minVolume", ""),
    [sort, setSort] = useWidgetState("sort", "market_cap"),
    [descending, setDescending] = useWidgetState("descending", true),
    [offset, setOffset] = useWidgetState("offset", 0);
  const params = new URLSearchParams({
    market,
    sort,
    descending: String(descending),
    offset: String(offset),
    limit: "100",
  });
  if (sector) params.set("sector", sector);
  if (cap) params.set("min_cap", cap);
  if (change) params.set("min_change", change);
  if (volume) params.set("min_volume", volume);
  const data = useMarket<MarketRow[]>(
    `/market/screener?${params}`,
    60000,
    active,
  );
  function order(field: string) {
    setOffset(0);
    setDescending((old) => (field === sort ? !old : true));
    setSort(field);
  }
  return (
    <>
      <div className="widget-filters">
        <select
          aria-label="Screener market"
          value={market}
          onChange={(e) => {
            setMarket(e.target.value);
            setOffset(0);
          }}
        >
          <option value="MY">Bursa / Malaysia</option>
          <option value="US">US equities</option>
        </select>
        <input
          aria-label="Sector filter"
          placeholder="Sector"
          value={sector}
          onChange={(e) => {
            setSector(e.target.value);
            setOffset(0);
          }}
        />
        {[
          ["Minimum market cap", cap, setCap],
          ["Minimum change %", change, setChange],
          ["Minimum volume", volume, setVolume],
        ].map(([label, value, set]) => (
          <input
            key={String(label)}
            type="number"
            aria-label={String(label)}
            placeholder={String(label)}
            value={String(value)}
            onChange={(e) => {
              (set as (v: string) => void)(e.target.value);
              setOffset(0);
            }}
          />
        ))}
      </div>
      <div className="widget-table">
        <table>
          <thead>
            <tr>
              {[
                ["symbol", "Symbol"],
                ["price", "Price"],
                ["market_cap", "Market cap"],
                ["change_percent", "Change %"],
                ["volume", "Volume"],
              ].map(([field, label]) => (
                <th key={field}>
                  <button onClick={() => order(field)}>
                    {label}
                    {sort === field ? (descending ? " ↓" : " ↑") : ""}
                  </button>
                </th>
              ))}
              <th>Sector / currency</th>
            </tr>
          </thead>
          <tbody>
            {data?.data?.map((row) => (
              <tr key={row.instrument.id}>
                <td>
                  <button onClick={() => onPick(row.instrument)}>
                    {row.instrument.symbol}
                  </button>
                </td>
                <td>{display(row.price)}</td>
                <td>{display(row.market_cap)}</td>
                <td>{display(row.change_percent)}%</td>
                <td>{display(row.volume)}</td>
                <td>
                  {row.sector} · {row.currency || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="inline">
        <button
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - 100))}
        >
          Previous
        </button>
        <span>
          Rows {offset + 1}–{offset + (data?.data?.length || 0)} /{" "}
          {String(data?.total ?? "unknown")}
        </span>
        <button
          disabled={!data?.data?.length || data.data.length < 100}
          onClick={() => setOffset(Math.min(20000, offset + 100))}
        >
          Next
        </button>
      </div>
      <MarketStatus value={data} />
    </>
  );
}
export function OptionsWidget({
  instrument,
  active,
}: {
  instrument: Instrument;
  active: boolean;
}) {
  const [expiry, setExpiry] = useWidgetState(`expiry:${instrument.id}`, "");
  const data = useMarket<{
    expiries: string[];
    expiry: string;
    calls: Record<string, unknown>[];
    puts: Record<string, unknown>[];
  }>(
    `/market/options?instrument=${encodeURIComponent(instrument.id)}${expiry ? `&expiry=${expiry}` : ""}`,
    30000,
    active,
  );
  const chain = data?.data && !Array.isArray(data.data) ? data.data : null;
  const strikes = [
    ...new Set(
      [...(chain?.calls || []), ...(chain?.puts || [])].map((r) =>
        String(r.strike),
      ),
    ),
  ].sort((a, b) => Number(a) - Number(b));
  return (
    <>
      <select
        aria-label="Option expiry"
        value={expiry || chain?.expiry || ""}
        onChange={(e) => setExpiry(e.target.value)}
      >
        {chain?.expiries.map((v) => (
          <option key={v}>{v}</option>
        ))}
      </select>
      <div className="widget-table">
        <table>
          <thead>
            <tr>
              <th>Call bid / ask</th>
              <th>Volume / OI</th>
              <th>Strike</th>
              <th>Put bid / ask</th>
              <th>Volume / OI</th>
            </tr>
          </thead>
          <tbody>
            {strikes.map((strike) => {
              const call = chain?.calls.find(
                  (r) => String(r.strike) === strike,
                ),
                put = chain?.puts.find((r) => String(r.strike) === strike);
              return (
                <tr key={strike}>
                  <td className={call?.itm ? "itm" : ""}>
                    {display(call?.bid)} / {display(call?.ask)}
                  </td>
                  <td>
                    {display(call?.volume)} / {display(call?.open_interest)}
                  </td>
                  <th>{display(strike)}</th>
                  <td className={put?.itm ? "itm" : ""}>
                    {display(put?.bid)} / {display(put?.ask)}
                  </td>
                  <td>
                    {display(put?.volume)} / {display(put?.open_interest)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <MarketStatus value={data} />
    </>
  );
}
export function CryptoWidget({
  active,
  onPick,
}: {
  active: boolean;
  onPick: (i: Instrument) => void;
}) {
  const data = useMarket<MarketRow[]>("/market/crypto", 30000, active);
  const dominance = data?.dominance as Record<string, number> | undefined;
  return (
    <>
      <p>
        BTC {display(dominance?.btc)}% · ETH {display(dominance?.eth)}%
        dominance
      </p>
      {data?.data?.map((row) => (
        <div className="crypto-row" key={row.instrument.id}>
          <button onClick={() => onPick(row.instrument)}>
            {row.instrument.symbol}
          </button>
          <span>{display(row.price, 4)} USD</span>
          <span>{display(row.change_percent)}%</span>
          <Sparkline values={row.sparkline || []} />
        </div>
      ))}
      <MarketStatus value={data} />
    </>
  );
}
function Sparkline({ values }: { values: number[] }) {
  if (!values.length) return <span>—</span>;
  const lo = Math.min(...values),
    hi = Math.max(...values);
  return (
    <svg width="90" height="24" aria-label="Seven-day sparkline">
      <polyline
        fill="none"
        stroke="#ff9900"
        points={values
          .map(
            (v, i) =>
              `${(i / Math.max(1, values.length - 1)) * 90},${22 - ((v - lo) / Math.max(1e-10, hi - lo)) * 20}`,
          )
          .join(" ")}
      />
    </svg>
  );
}
export function MacroWidget({ active }: { active: boolean }) {
  const data = useMarket<Record<string, unknown>[]>(
    "/market/macro",
    60000,
    active,
  );
  const yields = data?.data?.filter((r) => r.kind === "yield") || [];
  return (
    <>
      {yields.length > 0 && (
        <div className="yield-curve">
          {yields.map((row) => (
            <div key={String(row.name)}>
              <span>{String(row.name)}</span>
              <i
                style={{
                  height: `${Math.min(100, Math.max(2, Number(row.price) * 12))}px`,
                }}
              />
              <b>{display(row.price)}%</b>
            </div>
          ))}
        </div>
      )}
      {data?.data?.map((row) => (
        <div className="macro-row" key={String(row.name)}>
          <b>{String(row.name)}</b>
          <span>
            {display(row.price)} {String(row.unit || row.currency || "")}
          </span>
          <small>
            {String(row.kind)} · {String(row.observed_at || "Date unavailable")}
          </small>
        </div>
      ))}
      <MarketStatus value={data} />
    </>
  );
}
export function CalendarWidget({ active }: { active: boolean }) {
  const [tab, setTab] = useWidgetState("calendarTab", "economic");
  const terminal = useTerminal();
  const [asset, setAsset] = useWidgetState(
    "earningsInstrument",
    terminal.state.active.id,
  );
  const data = useMarket<Record<string, unknown>[]>(
    tab === "economic"
      ? "/market/calendar/economic"
      : `/market/calendar/earnings?instrument=${encodeURIComponent(asset)}`,
    900000,
    active,
  );
  return (
    <>
      <div className="inline">
        <button
          className={tab === "economic" ? "selected" : ""}
          onClick={() => setTab("economic")}
        >
          Economic
        </button>
        <button
          className={tab === "earnings" ? "selected" : ""}
          onClick={() => setTab("earnings")}
        >
          Earnings
        </button>
        {tab === "earnings" && (
          <select
            aria-label="Earnings instrument"
            value={asset}
            onChange={(e) => setAsset(e.target.value)}
          >
            {[
              ...new Map(
                [terminal.state.active, ...terminal.state.watchlist].map(
                  (i) => [i.id, i],
                ),
              ).values(),
            ].map((i) => (
              <option key={i.id} value={i.id}>
                {i.symbol}
              </option>
            ))}
          </select>
        )}
      </div>
      <div className="widget-table">
        <table>
          <thead>
            <tr>
              <th>Event / date</th>
              <th>Forecast</th>
              <th>Previous / actual</th>
              <th>{tab === "economic" ? "Outcome" : "Next-session move"}</th>
            </tr>
          </thead>
          <tbody>
            {data?.data?.map((row, index) => (
              <tr key={index}>
                <td>
                  {String(row.title || "Earnings")}
                  <small>
                    {String(row.date || "")} · {String(row.country || "")}
                  </small>
                </td>
                <td>{String(row.forecast ?? row.eps_estimate ?? "—")}</td>
                <td>{String(row.previous ?? row.eps_actual ?? "—")}</td>
                <td>
                  {row.actual !== undefined
                    ? String(row.actual ?? "Not verified")
                    : row.next_session_move_percent !== null
                      ? display(row.next_session_move_percent) + "%"
                      : "Not verified"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <MarketStatus value={data} />
    </>
  );
}
