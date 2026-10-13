import { amount } from "../../shared/api";
import { Document, Summary } from "./types";
export function HistoryView({
  doc,
  summary,
  currencyCode,
}: {
  doc: Document;
  summary: Summary | null;
  currencyCode: string;
}) {
  return (
    <section className="panel">
      <h2>Saved valuation history</h2>
      {summary?.performance_reset_date && (
        <p className="notice">
          Transactions corrected on {summary.performance_reset_date}. Earlier
          valuations are retained for reference and excluded from recorded
          performance.
        </p>
      )}
      <table>
        <thead>
          <tr>
            <th>Date</th>
            <th>Market value</th>
            <th>Cost basis</th>
          </tr>
        </thead>
        <tbody>
          {summary?.history.map((p) => (
            <tr key={p.date}>
              <td>{p.date}</td>
              <td>{amount(p.market_value, currencyCode)}</td>
              <td>{amount(p.cost_basis, currencyCode)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!!doc.transaction_corrections?.length && (
        <>
          <h3>Recent transaction corrections</h3>
          <p className="notice">
            Last 20 corrections. Original recorded values are retained.
          </p>
          <table>
            <thead>
              <tr>
                <th>Corrected (UTC)</th>
                <th>Symbol / Type</th>
                <th>Original quantity @ price</th>
                <th>Corrected quantity @ price</th>
              </tr>
            </thead>
            <tbody>
              {doc.transaction_corrections
                .slice(-20)
                .reverse()
                .map((entry, index) => (
                  <tr key={index}>
                    <td>{entry.corrected_at.slice(0, 19).replace("T", " ")}</td>
                    <td>
                      {entry.previous.symbol} / {entry.previous.side}
                    </td>
                    <td>
                      {entry.previous.quantity} @ {entry.previous.price}
                    </td>
                    <td>
                      {entry.replacement.quantity} @ {entry.replacement.price}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}
