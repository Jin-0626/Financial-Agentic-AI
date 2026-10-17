import { StockLabel } from "./stock-label";
import { useEffect, useRef, useState, type RefObject } from "react";
import {
  defaultTable,
  mutateWatchlist,
  namedLists,
  previewWatchlist,
  price,
  symbol,
  type BulkPreview,
  type PanelSpec,
  type QuoteColumn,
  type Snapshot,
  type WatchlistMutation,
} from "./bridge";
import { QuoteOwners } from "./subscriptions";
import { Dialog } from "./shared-ui";
export type WatchlistSort = "stored" | "symbol" | "price" | "change" | "volume";
const labels: Record<QuoteColumn, string> = {
  instrument: "Instrument",
  name: "Name",
  price: "Last",
  change: "Change %",
  volume: "Volume",
  currency: "Currency",
  exchange: "Exchange",
  status: "Data status",
};
export function compareDecimal(a: string, b: string): number {
  const parts = (s: string) => {
    const negative = s.startsWith("-");
    const [whole, fraction = ""] = s.replace(/^[+-]/, "").split(".");
    return {
      negative,
      digits: BigInt((whole || "0") + fraction),
      scale: fraction.length,
    };
  };
  const x = parts(a),
    y = parts(b),
    scale = Math.max(x.scale, y.scale),
    xv = x.digits * 10n ** BigInt(scale - x.scale) * (x.negative ? -1n : 1n),
    yv = y.digits * 10n ** BigInt(scale - y.scale) * (y.negative ? -1n : 1n);
  return xv < yv ? -1 : xv > yv ? 1 : 0;
}
function sorted(
  snapshot: Snapshot,
  ids: string[],
  sort: QuoteColumn | null,
  descending: boolean,
): string[] {
  if (!sort) return ids;
  const value = (id: string): string | null => {
    const market = snapshot.quotes[id],
      q = market?.data;
    switch (sort) {
      case "instrument":
        return symbol(id);
      case "name":
        return q?.instrument.name ?? null;
      case "price":
        return q?.price ?? null;
      case "change":
        return q?.change_percent ?? null;
      case "volume":
        return q?.volume ?? null;
      case "currency":
        return q?.currency ?? null;
      case "exchange":
        return q?.instrument.exchange ?? null;
      case "status":
        return market?.status ?? null;
    }
  };
  return [...ids].sort((a, b) => {
    const av = value(a),
      bv = value(b);
    if (av === null) return bv === null ? ids.indexOf(a) - ids.indexOf(b) : 1;
    if (bv === null) return -1;
    return (
      (descending ? -1 : 1) *
        (["price", "change", "volume"].includes(sort)
          ? compareDecimal(av, bv)
          : av.localeCompare(bv)) || ids.indexOf(a) - ids.indexOf(b)
    );
  });
}
export function sortSymbols(
  snapshot: Snapshot | null,
  sort: WatchlistSort,
  descending: boolean,
): string[] {
  return snapshot
    ? sorted(
        snapshot,
        snapshot.watchlist.symbols,
        sort === "stored" ? null : sort === "symbol" ? "instrument" : sort,
        descending,
      )
    : [];
}
export function Watchlist({
  snapshot,
  fullPage = false,
  spec,
  onChange,
  onSelect,
  owner,
  inputRef,
}: {
  snapshot: Snapshot;
  fullPage?: boolean;
  spec: PanelSpec;
  onChange: (spec: PanelSpec) => void;
  onSelect: (id: string, index: number) => void;
  owner: string;
  inputRef: RefObject<HTMLInputElement | null>;
}) {
  const lists = namedLists(snapshot),
    active = snapshot.preferences.active_watchlist_id ?? "main",
    list =
      lists.find((l) => l.id === (spec.watchlist_id ?? active)) ??
      lists.find((l) => l.id === active) ??
      lists[0];
  const [filter, setFilter] = useState(""),
    [rowMenu, setRowMenu] = useState<string | null>(null),
    [destination, setDestination] = useState("");
  const table = spec.table ?? defaultTable(),
    ids = sorted(snapshot, list.symbols, table.sort, table.descending).filter(
      (id) =>
        `${symbol(id)} ${snapshot.quotes[id]?.data?.instrument.name ?? ""}`
          .toLocaleLowerCase()
          .includes(filter.trim().toLocaleLowerCase()),
    );
  const [cursor, setCursor] = useState<string | null>(null),
    [text, setText] = useState(""),
    [name, setName] = useState(""),
    [error, setError] = useState<string | null>(null),
    [busy, setBusy] = useState(false),
    [bulk, setBulk] = useState(false),
    [bulkText, setBulkText] = useState(""),
    [preview, setPreview] = useState<BulkPreview | null>(null);
  const dragged = useRef<string | null>(null),
    current = cursor && ids.includes(cursor) ? cursor : (ids[0] ?? null);
  useEffect(() => setPreview(null), [list.id, list.symbols.join("|")]);
  const change = async (mutation: WatchlistMutation, done?: () => void) => {
    setBusy(true);
    setError(null);
    try {
      await mutateWatchlist(mutation);
      done?.();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  const activate = () => {
    if (active !== list.id && !busy)
      void change({ type: "activate", id: list.id });
  };
  const updateTable = (patch: Partial<typeof table>) =>
    onChange({ ...spec, table: { ...table, ...patch } });
  const reorder = (id: string, target: string) => {
    if (table.sort) return;
    const members = [...list.symbols],
      a = members.indexOf(id),
      b = members.indexOf(target);
    if (a < 0 || b < 0 || a === b) return;
    members.splice(a, 1);
    members.splice(b, 0, id);
    void change({ type: "reorder", id: list.id, symbols: members });
  };
  const move = (direction: number) => {
    if (current) {
      const target = list.symbols[list.symbols.indexOf(current) + direction];
      if (target) reorder(current, target);
    }
  };
  const create = (duplicate: boolean) => {
    const id = crypto.randomUUID();
    void change(
      { type: "create", id, name, duplicate_from: duplicate ? list.id : null },
      () => {
        setName("");
        onChange({ ...spec, watchlist_id: id });
      },
    );
  };
  const cell = (id: string, column: QuoteColumn) => {
    const market = snapshot.quotes[id],
      q = market?.data;
    switch (column) {
      case "instrument":
        return (
          <button
            className="instrument-button"
            onClick={() => {
              setCursor(id);
              activate();
              onSelect(id, ids.indexOf(id));
            }}
          >
            <StockLabel code={id} name={q?.instrument.name} />
            <small>
              {market?.stale
                ? "Stale"
                : q
                  ? (q.instrument.exchange ?? "—")
                  : market?.status === "unavailable"
                    ? "Unavailable"
                    : "Loading"}
            </small>
          </button>
        );
      case "name":
        return q?.instrument.name ?? "—";
      case "price":
        return price(q?.price);
      case "change":
        return (
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
        );
      case "volume":
        return price(q?.volume, 0);
      case "currency":
        return q?.currency ?? "—";
      case "exchange":
        return q?.instrument.exchange ?? "—";
      case "status":
        return (
          <span className={`data-state ${market?.status ?? ""}`}>
            {market?.status ?? "Loading"}
          </span>
        );
    }
  };
  return (
    <section
      className={`panel watchlist ${fullPage ? "watchlist-full" : ""}`}
      tabIndex={0}
      aria-label={`${list.name} watchlist`}
      onFocus={activate}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget))
          setRowMenu(null);
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          setRowMenu(null);
          return;
        }
        if (rowMenu) return;
        if (
          event.target instanceof HTMLInputElement ||
          event.target instanceof HTMLTextAreaElement ||
          event.target instanceof HTMLSelectElement
        )
          return;
        const binding = snapshot.preferences.keymap.find(({ key }) =>
          typeof key === "string"
            ? key ===
              (
                { ArrowUp: "Up", ArrowDown: "Down", Enter: "Enter" } as Record<
                  string,
                  string
                >
              )[event.key]
            : "Char" in key
              ? key.Char === event.key && !event.ctrlKey
              : event.ctrlKey && key.Ctrl === event.key.toLowerCase(),
        );
        if (binding?.operation === "Up" || binding?.operation === "Down") {
          event.preventDefault();
          event.stopPropagation();
          setCursor(
            ids[
              Math.max(
                0,
                Math.min(
                  ids.length - 1,
                  ids.indexOf(current ?? "") +
                    (binding.operation === "Up" ? -1 : 1),
                ),
              )
            ] ?? null,
          );
        }
        if (
          binding?.operation === "Select" &&
          !(event.target instanceof HTMLButtonElement) &&
          current
        ) {
          event.preventDefault();
          event.stopPropagation();
          onSelect(current, ids.indexOf(current));
        }
      }}
    >
      <QuoteOwners ids={list.symbols} owner={`${owner}:members:${list.id}`} />
      {fullPage && (
        <div
          className="page-tabs watchlist-list-tabs"
          role="tablist"
          aria-label="Watchlist lists"
        >
          {lists.map((item) => (
            <button
              role="tab"
              key={item.id}
              aria-selected={list.id === item.id}
              onClick={() =>
                void change({ type: "activate", id: item.id }, () =>
                  onChange({ ...spec, watchlist_id: item.id }),
                )
              }
            >
              {item.name}
            </button>
          ))}
        </div>
      )}
      <div className="panel-head">
        <h3>
          Watchlist <span>{ids.length}</span>
        </h3>
        <span className="tiny-label">LOCAL</span>
      </div>
      <div className="watchlist-controls">
        <div className="watchlist-toolbar">
          <select
            aria-label="Named watchlist"
            value={list.id}
            disabled={busy}
            onChange={(e) => {
              const id = e.target.value;
              void change({ type: "activate", id }, () =>
                onChange({ ...spec, watchlist_id: id }),
              );
            }}
          >
            {lists.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
          <details className="watchlist-menu">
            <summary>Manage</summary>
            <div>
              <input
                aria-label="Watchlist name"
                placeholder="List name"
                value={name}
                maxLength={64}
                onChange={(e) => setName(e.target.value)}
              />
              <button
                disabled={busy || !name.trim() || lists.length >= 20}
                onClick={() => create(false)}
              >
                Create list
              </button>
              <button
                disabled={busy || !name.trim() || lists.length >= 20}
                onClick={() => create(true)}
              >
                Duplicate list
              </button>
              <button
                disabled={busy || !name.trim()}
                onClick={() =>
                  void change({ type: "rename", id: list.id, name }, () =>
                    setName(""),
                  )
                }
              >
                Rename list
              </button>
              <button
                disabled={busy || lists.length === 1}
                onClick={() => void change({ type: "delete", id: list.id })}
              >
                Delete {list.name}
              </button>
              {[-1, 1].map((direction) => (
                <button
                  key={direction}
                  disabled={
                    busy ||
                    lists.indexOf(list) + direction < 0 ||
                    lists.indexOf(list) + direction >= lists.length
                  }
                  onClick={() => {
                    const order = lists.map((l) => l.id),
                      index = order.indexOf(list.id);
                    [order[index], order[index + direction]] = [
                      order[index + direction],
                      order[index],
                    ];
                    void change({ type: "order", ids: order });
                  }}
                >
                  Move list {direction < 0 ? "up" : "down"}
                </button>
              ))}
            </div>
          </details>
        </div>
        <div className="watchlist-sort">
          <select
            aria-label="Sort watchlist"
            value={table.sort ?? "stored"}
            onChange={(e) =>
              updateTable({
                sort:
                  e.target.value === "stored"
                    ? null
                    : (e.target.value as QuoteColumn),
              })
            }
          >
            <option value="stored">Saved order</option>
            {Object.entries(labels).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
          <button
            aria-label="Reverse watchlist sort"
            disabled={!table.sort}
            onClick={() => updateTable({ descending: !table.descending })}
          >
            {table.descending ? "↓" : "↑"}
          </button>
          <details className="watchlist-menu">
            <summary>Columns</summary>
            <div>
              {Object.entries(labels).map(([key, label]) => {
                const column = key as QuoteColumn,
                  index = table.columns.indexOf(column);
                return (
                  <div className="column-control" key={key}>
                    <label>
                      <input
                        type="checkbox"
                        checked={index >= 0}
                        disabled={column === "instrument"}
                        onChange={() =>
                          updateTable({
                            columns:
                              index >= 0
                                ? table.columns.filter((c) => c !== column)
                                : [...table.columns, column],
                          })
                        }
                      />
                      {label}
                    </label>
                    {index >= 0 &&
                      [-1, 1].map((d) => (
                        <button
                          key={d}
                          aria-label={`Move ${label} column ${d < 0 ? "left" : "right"}`}
                          disabled={
                            index + d < 0 || index + d >= table.columns.length
                          }
                          onClick={() => {
                            const columns = [...table.columns];
                            [columns[index], columns[index + d]] = [
                              columns[index + d],
                              columns[index],
                            ];
                            updateTable({ columns });
                          }}
                        >
                          {d < 0 ? "←" : "→"}
                        </button>
                      ))}
                  </div>
                );
              })}
            </div>
          </details>
        </div>
        <form
          className="add-symbol"
          onSubmit={(event) => {
            event.preventDefault();
            if (!text.trim()) return;
            setBusy(true);
            void previewWatchlist(list.id, text)
              .then((result) => {
                if (result.invalid.length || result.error)
                  throw new Error(
                    result.error ?? `Invalid: ${result.invalid.join(", ")}`,
                  );
                return change(
                  { type: "add", id: list.id, symbols: result.symbols },
                  () => setText(""),
                );
              })
              .catch((e) => setError(String(e)))
              .finally(() => setBusy(false));
          }}
        >
          <input
            ref={inputRef}
            disabled={busy}
            aria-label="Add symbol"
            placeholder="AAPL or 5326.KL"
            value={text}
            maxLength={128}
            onChange={(e) => setText(e.target.value)}
          />
          <button aria-label="Add to watchlist" disabled={busy}>
            +
          </button>
        </form>
        <div className="watchlist-row-tools">
          <button onClick={() => setBulk(true)}>Bulk add</button>
          <button
            disabled={
              busy ||
              !!table.sort ||
              !current ||
              list.symbols.indexOf(current) === 0
            }
            onClick={() => move(-1)}
          >
            Move up
          </button>
          <button
            disabled={
              busy ||
              !!table.sort ||
              !current ||
              list.symbols.indexOf(current) === list.symbols.length - 1
            }
            onClick={() => move(1)}
          >
            Move down
          </button>
        </div>
        {error && (
          <p role="alert" className="negative">
            {error}
          </p>
        )}
        <div className="watchlist-filter">
          <input
            aria-label="Filter watchlist"
            placeholder="Filter symbol or name"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          {filter && (
            <button onClick={() => setFilter("")}>Clear filter</button>
          )}
          <span>
            {ids.length}/{list.symbols.length}
          </span>
        </div>
      </div>
      {snapshot.preferences.selected &&
        !ids.includes(snapshot.preferences.selected) && (
          <p className="coverage-note">
            Active instrument is outside these results; chart selection is
            unchanged.
          </p>
        )}
      {rowMenu && (
        <div
          className="watchlist-row-menu"
          role="group"
          aria-label={`Actions for ${symbol(rowMenu)}`}
        >
          <StockLabel
            code={rowMenu}
            name={snapshot.quotes[rowMenu]?.data?.instrument.name}
          />
          <button
            disabled={busy}
            onClick={() => {
              onSelect(rowMenu, ids.indexOf(rowMenu));
              setRowMenu(null);
            }}
          >
            Open Instrument
          </button>
          <label>
            Add to another list
            <select
              aria-label="Destination watchlist"
              value={destination}
              onChange={(e) => setDestination(e.target.value)}
            >
              <option value="">Choose list</option>
              {lists
                .filter((l) => l.id !== list.id)
                .map((l) => (
                  <option value={l.id} key={l.id}>
                    {l.name}
                  </option>
                ))}
            </select>
          </label>
          <button
            disabled={
              busy ||
              !lists.some((l) => l.id === destination && l.id !== list.id) ||
              !!lists
                .find((l) => l.id === destination)
                ?.symbols.includes(rowMenu)
            }
            onClick={() =>
              void change(
                { type: "add", id: destination, symbols: [rowMenu] },
                () => setRowMenu(null),
              )
            }
          >
            Add to List
          </button>
          <button
            disabled={busy}
            onClick={() =>
              void change(
                { type: "remove", id: list.id, symbol: rowMenu },
                () => setRowMenu(null),
              )
            }
          >
            Remove from This List
          </button>
          {[-1, 1].map((direction) => (
            <button
              key={direction}
              disabled={
                busy ||
                !!table.sort ||
                list.symbols.indexOf(rowMenu) + direction < 0 ||
                list.symbols.indexOf(rowMenu) + direction >= list.symbols.length
              }
              onClick={() => {
                const target =
                  list.symbols[list.symbols.indexOf(rowMenu) + direction];
                if (target) reorder(rowMenu, target);
              }}
            >
              Move {direction < 0 ? "Up" : "Down"}
            </button>
          ))}
          <button onClick={() => setRowMenu(null)}>Close row actions</button>
        </div>
      )}
      <div className="watchlist-table-scroll">
        <table>
          <thead>
            <tr>
              <th aria-label="Reorder" />
              {table.columns.map((c) => (
                <th
                  key={c}
                  data-column={c}
                  aria-sort={
                    table.sort === c
                      ? table.descending
                        ? "descending"
                        : "ascending"
                      : "none"
                  }
                >
                  <button
                    onClick={() =>
                      updateTable({
                        sort: c,
                        descending:
                          table.sort === c ? !table.descending : false,
                      })
                    }
                  >
                    {labels[c]}
                  </button>
                </th>
              ))}
              <th />
            </tr>
          </thead>
          <tbody>
            {ids.map((id) => (
              <tr
                key={id}
                className={`${id === snapshot.preferences.selected ? "selected " : ""}${id === current ? "cursor" : ""}`}
                onClick={(event) => {
                  setCursor(id);
                  if (
                    !(event.target instanceof Element) ||
                    !event.target.closest("button,input,select,summary")
                  )
                    onSelect(id, ids.indexOf(id));
                }}
                onContextMenu={(event) => {
                  event.preventDefault();
                  setCursor(id);
                  setRowMenu(id);
                  event.currentTarget.focus();
                }}
                tabIndex={-1}
                onDragOver={(e) => {
                  if (!table.sort) e.preventDefault();
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  if (dragged.current) reorder(dragged.current, id);
                  dragged.current = null;
                }}
              >
                <td>
                  <button
                    className="row-drag"
                    draggable={!table.sort && !busy}
                    aria-label={`Drag ${symbol(id)}`}
                    disabled={!!table.sort || busy}
                    onDragStart={(e) => {
                      dragged.current = id;
                      e.dataTransfer.setData("text/plain", id);
                    }}
                    onDragEnd={() => {
                      dragged.current = null;
                    }}
                  >
                    ⠿
                  </button>
                </td>
                {table.columns.map((c) => (
                  <td key={c} data-column={c}>
                    {cell(id, c)}
                  </td>
                ))}
                <td>
                  <button
                    aria-label={`Actions for ${symbol(id)}`}
                    aria-expanded={rowMenu === id}
                    onClick={() => {
                      setCursor(id);
                      setRowMenu(rowMenu === id ? null : id);
                    }}
                  >
                    ⋯
                  </button>
                  <button
                    className="remove"
                    aria-label={`Remove ${symbol(id)}`}
                    disabled={busy}
                    onClick={() =>
                      void change({ type: "remove", id: list.id, symbol: id })
                    }
                  >
                    ×
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!ids.length && (
        <div className="empty">
          {list.symbols.length
            ? "No instruments match this filter."
            : "Your watchlist is empty. Add an instrument to begin."}
        </div>
      )}
      <div className="watchlist-hint">
        ↑ ↓ navigate <span>↵ select</span>
      </div>
      {bulk && (
        <Dialog
          busy={busy}
          title={`Bulk add · ${list.name}`}
          onClose={() => {
            if (!busy) setBulk(false);
          }}
        >
          <p>
            Separate instrument IDs or equity symbols with commas, semicolons,
            or new lines.
          </p>
          <textarea
            disabled={busy}
            aria-label="Bulk symbols"
            value={bulkText}
            maxLength={16384}
            onChange={(e) => {
              setBulkText(e.target.value);
              setPreview(null);
            }}
          />
          <button
            disabled={busy || !bulkText.trim()}
            onClick={() => {
              setBusy(true);
              setError(null);
              void previewWatchlist(list.id, bulkText)
                .then(setPreview)
                .catch((e) => setError(String(e)))
                .finally(() => setBusy(false));
            }}
          >
            Preview symbols
          </button>
          {preview && (
            <section aria-label="Bulk preview">
              <p>New: {preview.symbols.join(", ") || "None"}</p>
              <p>
                Duplicates skipped: {preview.duplicates.join(", ") || "None"}
              </p>
              <p>Invalid: {preview.invalid.join(", ") || "None"}</p>
              {preview.error && <p role="alert">{preview.error}</p>}
              <button
                disabled={
                  busy ||
                  !!preview.error ||
                  !!preview.invalid.length ||
                  !preview.symbols.length
                }
                onClick={() =>
                  void change(
                    { type: "add", id: list.id, symbols: preview.symbols },
                    () => {
                      setBulk(false);
                      setBulkText("");
                      setPreview(null);
                    },
                  )
                }
              >
                Commit symbols
              </button>
            </section>
          )}
          {error && (
            <p role="alert" className="negative">
              {error}
            </p>
          )}
        </Dialog>
      )}
    </section>
  );
}
