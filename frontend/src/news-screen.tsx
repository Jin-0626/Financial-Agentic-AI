import { useState } from "react";
import { useResource } from "./resources";
import { DataInfo } from "./data-info";
export function NewsScreen({
  selected,
  mode,
}: {
  selected: string | null;
  mode: string;
}) {
  const [global, setGlobal] = useState(true),
    [chosen, setChosen] = useState<string | null>(null);
  const result = useResource(
      { type: "news", instrument: global ? null : selected },
      mode === "HTTP",
      "news:page",
    ),
    market = result.payload?.type === "news" ? result.payload.market : null,
    rows = market?.data ?? [],
    article = rows.find((row) => row.url === chosen) ?? rows[0];
  return (
    <section className="news-page">
      <div className="page-tabs">
        <button
          aria-pressed={global}
          onClick={() => {
            setGlobal(true);
            setChosen(null);
          }}
        >
          Global headlines
        </button>
        <button
          disabled={!selected}
          aria-pressed={!global}
          onClick={() => {
            setGlobal(false);
            setChosen(null);
          }}
        >
          Selected instrument
        </button>
      </div>
      {market && <DataInfo market={market} />}{" "}
      {result.error && <p role="alert">{result.error}</p>}
      {mode !== "HTTP" ? (
        <div className="empty">Live screens require HTTP mode.</div>
      ) : (
        <div className="news-body">
          <aside className="headline-list">
            {rows.map((row) => (
              <button
                key={row.url}
                aria-pressed={article?.url === row.url}
                onClick={() => setChosen(row.url)}
              >
                <strong>{row.title}</strong>
                <small>
                  {row.publisher ?? "Publisher unavailable"} ·{" "}
                  {row.published_at ?? "Date unavailable"}
                </small>
              </button>
            ))}
            {!rows.length && (
              <div className="empty">
                {result.loading
                  ? "Loading headlines…"
                  : "No headlines returned"}
              </div>
            )}
          </aside>
          <article className="news-detail">
            {article && (
              <>
                <h2>{article.title}</h2>
                <p>
                  {article.publisher ?? "Publisher unavailable"} ·{" "}
                  {article.published_at ?? "Date unavailable"}
                </p>
                <p>
                  This provider supplies headline metadata. Read the full
                  article at its publisher.
                </p>
                <a
                  href={
                    /^https?:\/\//i.test(article.url) ? article.url : undefined
                  }
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Read original article ↗
                </a>
              </>
            )}
          </article>
        </div>
      )}
    </section>
  );
}
