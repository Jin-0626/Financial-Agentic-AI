import { amount } from "../../shared/api";
import { Document, Summary } from "./types";
export function TransactionsView({
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
      <h2>Trade records</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Date</th>
              <th>Symbol</th>
              <th>Type</th>
              <th>Quantity</th>
              <th>Price</th>
              <th>Realized P&L</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {doc.transactions.map((t) => (
              <tr key={t.id}>
                <td>{t.date}</td>
                <td>{t.symbol}</td>
                <td className={t.side === "SELL" ? "negative" : "positive"}>
                  {t.side}
                </td>
                <td>{t.quantity}</td>
                <td>{amount(t.price, currencyCode)}</td>
                <td>{amount(t.realized_gain, currencyCode)}</td>
                <td>{t.notes}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
