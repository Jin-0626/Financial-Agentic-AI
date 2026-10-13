import { amount, number } from "../../shared/api";
import { Holding, Document } from "./types";
type Props = {
  positions: Holding[];
  currencyCode: string;
  doc: Document | null;
  busy: boolean;
  editHolding: (holding: Holding) => void;
};
export function HoldingsTable({
  positions,
  currencyCode,
  doc,
  busy,
  editHolding,
}: Props) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {[
              "Symbol / Company",
              "Quantity",
              "Average cost",
              "Quote",
              "Market value",
              "P&L",
              "Weight",
              "Market / As of",
              "Actions",
            ].map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr key={p.symbol}>
              <td>
                <b>{p.symbol}</b>
                <small>{p.name}</small>
              </td>
              <td>{p.quantity}</td>
              <td>{amount(p.average_cost, currencyCode)}</td>
              <td>{amount(p.price, currencyCode)}</td>
              <td>{amount(p.market_value, currencyCode)}</td>
              <td
                className={
                  (number(p.unrealized_gain) || 0) < 0 ? "negative" : "positive"
                }
              >
                {amount(p.unrealized_gain, currencyCode)}
              </td>
              <td>
                {p.weight_percent
                  ? `${Number(p.weight_percent).toFixed(1)}%`
                  : "—"}
              </td>
              <td>
                {p.market_state || "Unavailable"}
                <small>
                  {p.as_of
                    ? new Date(p.as_of * 1000).toLocaleString()
                    : "Quote time not supplied"}
                </small>
              </td>
              <td>
                <button
                  disabled={
                    busy ||
                    !doc?.transactions.some((t) => t.symbol === p.symbol)
                  }
                  aria-label={`Edit holding ${p.symbol}`}
                  title={
                    doc?.transactions.some((t) => t.symbol === p.symbol)
                      ? "Correct a recorded transaction"
                      : "No recorded transactions to edit for this holding"
                  }
                  onClick={() => editHolding(p)}
                >
                  Edit
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
