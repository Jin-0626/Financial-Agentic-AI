import { useEffect, useRef, useState, type ReactNode } from "react";
export function DataTable({
  columns,
  rows,
}: {
  columns: string[];
  rows: ReactNode[][];
}) {
  const [page, setPage] = useState(0),
    size = 100,
    max = Math.max(0, Math.ceil(rows.length / size) - 1);
  useEffect(() => setPage((value) => Math.min(value, max)), [max]);
  const current = Math.min(page, max);
  return (
    <div
      className="resource-table"
      role="region"
      aria-label={`${columns[0] ?? "Data"} records`}
      tabIndex={0}
    >
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column} scope="col">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows
            .slice(current * size, (current + 1) * size)
            .map((row, index) => (
              <tr key={current * size + index}>
                {row.map((cell, i) => (
                  <td key={i}>{cell ?? "—"}</td>
                ))}
              </tr>
            ))}
        </tbody>
      </table>
      {!rows.length && <div className="empty">No records returned</div>}
      {rows.length > size && (
        <div className="table-pagination">
          <button
            disabled={!current}
            onClick={() => setPage((value) => value - 1)}
          >
            Previous rows
          </button>
          <span>
            {current * size + 1}–{Math.min(rows.length, (current + 1) * size)}{" "}
            of {rows.length}
          </span>
          <button
            disabled={current >= max}
            onClick={() => setPage((value) => value + 1)}
          >
            Next rows
          </button>
        </div>
      )}
    </div>
  );
}
export function Dialog({
  title,
  children,
  onClose,
  busy = false,
  className = "",
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  busy?: boolean;
  className?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    const focused = document.activeElement as HTMLElement | null;
    if (dialog && !dialog.open) dialog.showModal();
    return () => {
      dialog?.close();
      focused?.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className={`workflow-dialog ${className}`}
      aria-label={title}
      tabIndex={-1}
      onKeyDown={(event) => {
        if (event.key !== "Tab") return;
        const dialog = event.currentTarget;
        const items = [
          ...dialog.querySelectorAll<HTMLElement>(
            'button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],[tabindex]:not([tabindex="-1"])',
          ),
        ].filter((item) => item.getClientRects().length > 0);
        const first = items[0],
          last = items[items.length - 1];
        if (!first) {
          event.preventDefault();
          dialog.focus();
        } else if (
          event.shiftKey &&
          (document.activeElement === first ||
            !dialog.contains(document.activeElement))
        ) {
          event.preventDefault();
          last.focus();
        } else if (
          !event.shiftKey &&
          (document.activeElement === last ||
            !dialog.contains(document.activeElement))
        ) {
          event.preventDefault();
          first.focus();
        }
      }}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <header>
        <h2>{title}</h2>
        <button
          type="button"
          disabled={busy}
          aria-label="Close dialog"
          onClick={onClose}
        >
          ×
        </button>
      </header>
      {children}
    </dialog>
  );
}
export function EquityChart({
  rows,
  currency,
}: {
  rows: { date: string; equity: string }[];
  currency: string;
}) {
  const points = rows
    .map((row, index) => ({ ...row, index, value: Number(row.equity) }))
    .filter((row) => Number.isFinite(row.value));
  if (points.length < 2) return <p>No equity series available</p>;
  const low = Math.min(...points.map((row) => row.value)),
    high = Math.max(...points.map((row) => row.value)),
    spread = Math.max(high - low, 1);
  const sampled = points.filter(
    (_, index) =>
      index % Math.max(1, Math.ceil(points.length / 2000)) === 0 ||
      index === points.length - 1,
  );
  const path = sampled
    .map(
      (row, index) =>
        `${index ? "L" : "M"}${35 + (row.index / Math.max(rows.length - 1, 1)) * 800},${210 - ((row.value - low) / spread) * 170}`,
    )
    .join(" ");
  return (
    <figure className="equity-chart">
      <figcaption>Equity curve · {currency}</figcaption>
      <svg viewBox="0 0 900 250" role="img" aria-label="Backtest equity curve">
        <path d={path} fill="none" stroke="var(--cyan)" strokeWidth="2" />
        <text x="35" y="238" className="axis">
          {points[0].date}
        </text>
        <text x="835" y="238" textAnchor="end" className="axis">
          {points[points.length - 1].date}
        </text>
        <text x="845" y="40" className="axis">
          {high.toFixed(2)}
        </text>
        <text x="845" y="210" className="axis">
          {low.toFixed(2)}
        </text>
      </svg>
    </figure>
  );
}
