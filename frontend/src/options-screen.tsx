import { StockLabel } from "./stock-label";
import { useEffect, useRef, useState } from "react";
import { price, type Range } from "./bridge";
import { useResource, type Contract } from "./resources";
import { MarketChart } from "./chart";
import { DataInfo } from "./data-info";
import { Dialog } from "./shared-ui";
import {
  BrowserSaveStatus,
  ColumnControls,
  PresetControls,
  useBrowserSettings,
} from "./browser-settings";
import {
  optionLabels,
  type OptionColumn,
  type OptionsSettings,
} from "./browser-model";
import {
  decimalParts,
  nonnegativeInteger,
  percent,
  normalizeDecimal,
} from "./decimal";
import { filterChain, type SelectedContract } from "./options-model";
const sessionExpiries = new Map<string, string>();
export { alignContracts } from "./options-model";
export function OptionsScreen({
  selected,
  mode,
}: {
  selected: string | null;
  mode: string;
}) {
  const {
      preferences,
      mutate,
      pending,
      error: saveError,
    } = useBrowserSettings(),
    settings = preferences.options,
    disabled = pending || !!saveError;
  const [expiriesBySymbol, setExpiries] = useState<Record<string, string>>({}),
    [range, setRange] = useState<Range>("6M"),
    [storedPick, setPicked] = useState<
      | (SelectedContract & { symbol: string | null; requestExpiry: string })
      | null
    >(null),
    [form, setForm] = useState(settings),
    [wide, setWide] = useState(
      () => window.matchMedia("(min-width:1200px)").matches,
    );
  const restoreFocus = useRef<HTMLElement | null>(null),
    expiry = selected
      ? (expiriesBySymbol[selected] ?? sessionExpiries.get(selected) ?? "")
      : "";
  useEffect(() => {
    const media = window.matchMedia("(min-width:1200px)");
    const change = () => setWide(media.matches);
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, []);
  useEffect(() => setForm(settings), [settings]);
  useEffect(() => setPicked(null), [selected, expiry]);
  const result = useResource(
    selected
      ? { type: "options", instrument: selected, expiry: expiry || null }
      : null,
    mode === "HTTP",
    "options:chain",
  );
  const quote = useResource(
    selected ? { type: "quote", instrument: selected } : null,
    mode === "HTTP",
    "options:quote",
  );
  const market =
      result.payload?.type === "options" ? result.payload.market : null,
    chain = market?.data,
    underlying =
      quote.payload?.type === "quote"
        ? (quote.payload.market.data?.price ?? null)
        : null,
    hasPrice = underlying !== null && decimalParts(underlying) !== null;
  const effective =
    settings.strike_window === "nearest21" && !hasPrice
      ? { ...settings, strike_window: "all" as const }
      : settings;
  const rows = chain ? filterChain(chain, effective, underlying) : [];
  const selectedRow =
    storedPick &&
    storedPick.symbol === selected &&
    storedPick.requestExpiry === expiry
      ? rows.find(
          (row) =>
            row.strike ===
            (storedPick.contract.strike === null
              ? null
              : normalizeDecimal(storedPick.contract.strike)),
        )
      : null;
  const latestContract =
    storedPick && selectedRow
      ? selectedRow[storedPick.side][storedPick.occurrence]
      : null;
  const picked =
    storedPick && latestContract?.contract === storedPick.contract.contract
      ? { ...storedPick, contract: latestContract }
      : null;
  const history = useResource(
    selected ? { type: "history", instrument: selected, range } : null,
    mode === "HTTP" && wide && !picked,
    "options:history",
  );
  const expiryIndex = chain?.expiries.indexOf(expiry || chain.expiry) ?? -1;
  const setExpiry = (value: string) => {
    if (selected) {
      sessionExpiries.set(selected, value);
      if (sessionExpiries.size > 128) {
        const first = sessionExpiries.keys().next().value;
        if (first) sessionExpiries.delete(first);
      }
      setExpiries((previous) => ({ ...previous, [selected]: value }));
    }
    setPicked(null);
  };
  const change = (patch: Partial<OptionsSettings>) => {
    setPicked(null);
    mutate({ type: "options", settings: { ...settings, ...patch } });
  };
  const close = () => {
    setPicked(null);
    restoreFocus.current?.focus();
  };
  const display = (contract: Contract, field: OptionColumn) =>
    field === "implied_volatility"
      ? contract[field] === null
        ? "—"
        : `${price(percent(contract[field]), 2)}%`
      : price(
          contract[field],
          field === "volume" || field === "open_interest" ? 0 : 2,
        );
  const contracts = (
    values: Contract[],
    field: OptionColumn,
    side: "calls" | "puts",
  ) =>
    values.length
      ? values.map((contract, index) => (
          <button
            className={`contract-cell ${contract.itm ? "in-the-money" : ""}`}
            key={`${contract.contract}:${index}`}
            title={contract.contract}
            aria-label={`${side === "calls" ? "Call" : "Put"} ${contract.contract} ${optionLabels[field]} ${index + 1}`}
            aria-pressed={
              picked?.side === side &&
              picked.contract.contract === contract.contract &&
              picked.occurrence === index
            }
            onClick={(event) => {
              restoreFocus.current = event.currentTarget;
              setPicked({
                side,
                contract,
                occurrence: index,
                symbol: selected,
                requestExpiry: expiry,
              });
            }}
          >
            {display(contract, field)}
          </button>
        ))
      : "—";
  return (
    <section className="options-workspace">
      <div className="options-chain">
        <div className="page-tabs">
          <span className="active">Chains</span>
        </div>
        <div className="feature-toolbar">
          <button
            aria-label="Previous option expiry"
            disabled={!chain || expiryIndex <= 0 || result.loading}
            onClick={() => chain && setExpiry(chain.expiries[expiryIndex - 1])}
          >
            ←
          </button>
          <label>
            Expiry
            <select
              aria-label="Option expiry"
              value={
                chain?.expiries.includes(expiry || chain.expiry)
                  ? expiry || chain.expiry
                  : ""
              }
              disabled={!chain?.expiries.length || result.loading}
              onChange={(e) => setExpiry(e.target.value)}
            >
              {!chain?.expiries.length && (
                <option value="">No provider expiries</option>
              )}
              {chain?.expiries.map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
          <button
            aria-label="Next option expiry"
            disabled={
              !chain ||
              expiryIndex < 0 ||
              expiryIndex >= chain.expiries.length - 1 ||
              result.loading
            }
            onClick={() => chain && setExpiry(chain.expiries[expiryIndex + 1])}
          >
            →
          </button>
          {expiry && (
            <button onClick={() => setExpiry("")}>
              Provider default expiry
            </button>
          )}
          <ColumnControls
            title="Option columns"
            columns={settings.columns}
            labels={optionLabels}
            onSave={(columns) => change({ columns })}
            disabled={disabled}
          />
          <PresetControls />
        </div>
        <form
          className="feature-toolbar option-filters"
          onSubmit={(e) => {
            e.preventDefault();
            if (
              nonnegativeInteger(form.min_volume ?? "") &&
              nonnegativeInteger(form.min_open_interest ?? "")
            )
              change({
                min_volume: form.min_volume || null,
                min_open_interest: form.min_open_interest || null,
                search: form.search,
              });
          }}
        >
          <label>
            Side
            <select
              aria-label="Option side"
              disabled={disabled}
              value={settings.side}
              onChange={(e) =>
                change({ side: e.target.value as OptionsSettings["side"] })
              }
            >
              <option value="both">Both</option>
              <option value="calls">Calls</option>
              <option value="puts">Puts</option>
            </select>
          </label>
          <label>
            Moneyness
            <select
              aria-label="Option moneyness"
              disabled={disabled}
              value={settings.moneyness}
              onChange={(e) =>
                change({
                  moneyness: e.target.value as OptionsSettings["moneyness"],
                })
              }
            >
              <option value="all">All</option>
              <option value="itm">ITM</option>
              <option value="otm">OTM</option>
            </select>
          </label>
          <label>
            Strikes
            <select
              aria-label="Option strike window"
              disabled={disabled}
              value={effective.strike_window}
              onChange={(e) =>
                change({
                  strike_window: e.target
                    .value as OptionsSettings["strike_window"],
                })
              }
            >
              <option value="all">All strikes</option>
              <option value="nearest21" disabled={!hasPrice}>
                Nearest 21 strikes
              </option>
            </select>
          </label>
          {(["min_volume", "min_open_interest"] as const).map((field) => (
            <label key={field}>
              {field === "min_volume" ? "Minimum volume" : "Minimum OI"}
              <input
                aria-label={
                  field === "min_volume"
                    ? "Option minimum volume"
                    : "Option minimum open interest"
                }
                value={form[field] ?? ""}
                disabled={disabled}
                maxLength={19}
                inputMode="numeric"
                pattern="[0-9]+"
                onChange={(e) => setForm({ ...form, [field]: e.target.value })}
              />
            </label>
          ))}
          <label>
            Contract search
            <input
              aria-label="Option contract search"
              maxLength={80}
              disabled={disabled}
              value={form.search}
              onChange={(e) => setForm({ ...form, search: e.target.value })}
            />
          </label>
          <button
            disabled={
              disabled ||
              !nonnegativeInteger(form.min_volume ?? "") ||
              !nonnegativeInteger(form.min_open_interest ?? "")
            }
          >
            Apply option filters
          </button>
        </form>
        <p className="coverage-note">
          Filters apply to the loaded expiry. IV is shown as a percentage.{" "}
          {hasPrice
            ? "Nearest-strike selection uses the underlying quote."
            : "Underlying price unavailable; nearest-strike mode is disabled."}
        </p>
        <BrowserSaveStatus />
        {market && <DataInfo market={market} />}
        {result.error && (
          <p role="alert" className="negative">
            {result.error}
          </p>
        )}
        {mode !== "HTTP" ? (
          <div className="empty">Live screens require HTTP mode.</div>
        ) : !selected ? (
          <div className="empty">Select an underlying instrument.</div>
        ) : !chain ? (
          <div className="empty">
            {result.loading
              ? "Loading option chain…"
              : "No option chain available"}
          </div>
        ) : (
          <>
            {result.loading && <p role="status">Refreshing option chain…</p>}
            {!chain.expiries.length && (
              <p className="coverage-note">
                No expiry choices returned by the provider.
              </p>
            )}
            <div className="options-table-scroll">
              <table className="options-table">
                <thead>
                  <tr>
                    {settings.side !== "puts" && (
                      <th colSpan={settings.columns.length}>Calls</th>
                    )}
                    <th className="strike">Strike</th>
                    {settings.side !== "calls" && (
                      <th colSpan={settings.columns.length}>Puts</th>
                    )}
                  </tr>
                  <tr>
                    {settings.side !== "puts" &&
                      [...settings.columns]
                        .reverse()
                        .map((column) => (
                          <th key={column}>{optionLabels[column]}</th>
                        ))}
                    <th className="strike">Price</th>
                    {settings.side !== "calls" &&
                      settings.columns.map((column) => (
                        <th key={column}>{optionLabels[column]}</th>
                      ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, index) => (
                    <tr key={row.strike ?? `missing-${index}`}>
                      {settings.side !== "puts" &&
                        [...settings.columns]
                          .reverse()
                          .map((column) => (
                            <td key={column}>
                              {contracts(row.calls, column, "calls")}
                            </td>
                          ))}
                      <th
                        className="strike"
                        scope="row"
                        title={row.strike ?? "Missing or invalid strike"}
                      >
                        {row.strike === null
                          ? "Strike unavailable"
                          : row.strike}
                      </th>
                      {settings.side !== "calls" &&
                        settings.columns.map((column) => (
                          <td key={column}>
                            {contracts(row.puts, column, "puts")}
                          </td>
                        ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              {!rows.length && (
                <div className="empty">
                  {chain.calls.length || chain.puts.length
                    ? "No contracts match these filters."
                    : "No contracts returned for this expiry."}
                </div>
              )}
            </div>
            <p className="coverage-note">
              {rows.length} strike groups · duplicate contracts retained. Select
              a quote field to inspect its contract.
            </p>
          </>
        )}
      </div>
      {wide && (
        <aside className="options-underlying">
          {picked ? (
            <ContractDetails
              picked={picked}
              expiry={chain?.expiry ?? expiry}
              stale={!!market?.stale}
              onClose={close}
            />
          ) : (
            <>
              <h3>
                Underlying{" "}
                {selected && (
                  <StockLabel
                    code={selected}
                    name={
                      quote.payload?.type === "quote"
                        ? quote.payload.market.data?.instrument.name
                        : null
                    }
                  />
                )}
              </h3>
              {quote.payload?.type === "quote" && (
                <>
                  <strong className="underlying-price">
                    {price(quote.payload.market.data?.price)}{" "}
                    {quote.payload.market.data?.currency}
                  </strong>
                  <DataInfo market={quote.payload.market} />
                </>
              )}
              {history.payload?.type === "history" ? (
                <MarketChart
                  market={history.payload.market}
                  range={range}
                  identity={selected ?? "none"}
                  name={
                    quote.payload?.type === "quote"
                      ? quote.payload.market.data?.instrument.name
                      : null
                  }
                  currency={
                    quote.payload?.type === "quote"
                      ? (quote.payload.market.data?.currency ?? null)
                      : null
                  }
                  onRange={setRange}
                />
              ) : (
                <div className="empty">
                  {history.error ??
                    (history.loading
                      ? "Loading underlying history…"
                      : "Underlying history unavailable.")}
                </div>
              )}
            </>
          )}
        </aside>
      )}
      {!wide && picked && (
        <Dialog
          title="Option contract details"
          className="contract-drawer"
          onClose={close}
        >
          <ContractDetails
            showHeader={false}
            picked={picked}
            expiry={chain?.expiry ?? expiry}
            stale={!!market?.stale}
            onClose={close}
          />
        </Dialog>
      )}
    </section>
  );
}
function ContractDetails({
  picked,
  expiry,
  stale,
  onClose,
  showHeader = true,
}: {
  showHeader?: boolean;
  picked: SelectedContract;
  expiry: string;
  stale: boolean;
  onClose: () => void;
}) {
  const contract = picked.contract;
  return (
    <section className="contract-details" aria-label="Selected option contract">
      {showHeader && (
        <header>
          <h3>Contract details</h3>
          <button aria-label="Close contract details" onClick={onClose}>
            ×
          </button>
        </header>
      )}
      {stale && <p className="negative">STALE CONTRACT DATA</p>}
      <dl>
        {[
          ["Contract", contract.contract],
          ["Side", picked.side === "calls" ? "Call" : "Put"],
          ["Expiry", expiry],
          ["Strike", contract.strike],
          ["Bid", contract.bid],
          ["Ask", contract.ask],
          ["Last", contract.last],
          ["Volume", contract.volume],
          ["Open interest", contract.open_interest],
          [
            "Implied volatility",
            contract.implied_volatility === null
              ? null
              : `${percent(contract.implied_volatility)}%`,
          ],
          ["Provider ITM flag", contract.itm ? "ITM" : "OTM"],
          ["Observed", contract.observed_at],
        ].map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value ?? "Unavailable"}</dd>
          </div>
        ))}
      </dl>
      <p className="coverage-note">
        Read-only provider fields. No order entry, Greeks or inferred contract
        quotes.
      </p>
    </section>
  );
}
