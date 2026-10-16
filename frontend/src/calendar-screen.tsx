import { useState } from "react";
import { MarketResource, useResource, type Calendar } from "./resources";
import { useDesktopView } from "./view-settings";
import { DataInfo } from "./data-info";
import { DataTable } from "./shared-ui";
function utcDay(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isFinite(date.getTime())
    ? date.toISOString().slice(0, 10)
    : null;
}
export function CalendarScreen({
  selected,
  mode,
}: {
  selected: string | null;
  mode: string;
}) {
  const { view, update } = useDesktopView(),
    [country, setCountry] = useState(""),
    [impact, setImpact] = useState(""),
    [day, setDay] = useState("");
  const result = useResource(
    { type: "calendar" },
    mode === "HTTP" && view.calendar_tab === "economic",
    "calendar:events",
  );
  const market =
      result.payload?.type === "calendar" ? result.payload.market : null,
    rows = market?.data ?? [];
  const today = new Date(),
    weekday = today.getUTCDay(),
    start = new Date(
      Date.UTC(
        today.getUTCFullYear(),
        today.getUTCMonth(),
        today.getUTCDate() - weekday,
      ),
    );
  const days = Array.from({ length: 7 }, (_, i) =>
    new Date(start.getTime() + i * 86400000).toISOString().slice(0, 10),
  );
  const filtered = rows.filter(
    (row) =>
      (!country || row.country === country) &&
      (!impact || row.impact === impact) &&
      (!day || utcDay(row.date) === day),
  );
  const event = (row: Calendar, index: number) => (
    <article
      className="calendar-event"
      key={`${row.date}:${row.title}:${index}`}
    >
      <strong>{row.title ?? "Untitled event"}</strong>
      <span>
        {row.country ?? "Country unavailable"} ·{" "}
        {row.impact ?? "Impact unavailable"}
      </span>
      <time>{row.date ?? "Time unavailable"}</time>
      <small>
        Actual {row.actual ?? "—"} · Forecast {row.forecast ?? "—"}
      </small>
      <small>{row.actual_status ?? "Evidence unavailable"}</small>
    </article>
  );
  return (
    <section className="calendar-workspace">
      <div className="page-tabs">
        <button
          aria-pressed={view.calendar_tab === "economic"}
          onClick={() => update({ calendar_tab: "economic" })}
        >
          Economic
        </button>
        <button
          aria-pressed={view.calendar_tab === "earnings"}
          onClick={() => update({ calendar_tab: "earnings" })}
        >
          Selected instrument earnings
        </button>
      </div>
      {mode !== "HTTP" ? (
        <div className="empty">Live screens require HTTP mode.</div>
      ) : view.calendar_tab === "earnings" ? (
        selected ? (
          <>
            <p className="coverage-note">
              Historical earnings for the selected instrument; not a market-wide
              forward schedule.
            </p>
            <MarketResource
              request={{ type: "earnings", instrument: selected }}
              onSelect={() => {}}
            />
          </>
        ) : (
          <div className="empty">Select an instrument to inspect earnings.</div>
        )
      ) : (
        <div className="calendar-body">
          <aside className="calendar-filters">
            <h3>Current week</h3>
            <p>
              {days[0]} – {days[6]} UTC
            </p>
            <label>
              Day
              <select
                aria-label="Calendar day"
                value={day}
                onChange={(e) => setDay(e.target.value)}
              >
                <option value="">All returned events</option>
                {days.map((date) => (
                  <option key={date}>{date}</option>
                ))}
              </select>
            </label>
            <label>
              Country
              <select
                aria-label="Calendar country"
                value={country}
                onChange={(e) => setCountry(e.target.value)}
              >
                <option value="">All</option>
                {[
                  ...new Set(
                    rows
                      .map((row) => row.country)
                      .filter((value): value is string => !!value),
                  ),
                ].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </label>
            <label>
              Impact
              <select
                aria-label="Calendar impact"
                value={impact}
                onChange={(e) => setImpact(e.target.value)}
              >
                <option value="">All</option>
                {[
                  ...new Set(
                    rows
                      .map((row) => row.impact)
                      .filter((value): value is string => !!value),
                  ),
                ].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </label>
            <div className="page-tabs">
              <button
                aria-pressed={view.calendar_mode === "week"}
                onClick={() => update({ calendar_mode: "week" })}
              >
                Week
              </button>
              <button
                aria-pressed={view.calendar_mode === "table"}
                onClick={() => update({ calendar_mode: "table" })}
              >
                Table
              </button>
            </div>
            <p className="coverage-note">
              Current-week public feed. Dates outside the feed are not
              available. Event times are shown as supplied; day columns use UTC.
            </p>
          </aside>
          <div className="calendar-content">
            {market && <DataInfo market={market} />}{" "}
            {result.error && <p role="alert">{result.error}</p>}
            {result.loading && !market && <p>Loading calendar…</p>}
            {view.calendar_mode === "week" ? (
              <>
                <div className="calendar-week">
                  {days.map((date) => (
                    <section key={date}>
                      <h3>
                        {new Date(`${date}T12:00:00Z`).toLocaleDateString(
                          "en",
                          {
                            weekday: "short",
                            month: "short",
                            day: "numeric",
                            timeZone: "UTC",
                          },
                        )}
                      </h3>
                      {filtered
                        .filter((row) => utcDay(row.date) === date)
                        .map(event)}
                    </section>
                  ))}
                </div>
                {filtered.filter(
                  (row) => !days.includes(utcDay(row.date) ?? ""),
                ).length > 0 && (
                  <section>
                    <h3>Other returned dates / time unavailable</h3>
                    {filtered
                      .filter((row) => !days.includes(utcDay(row.date) ?? ""))
                      .map(event)}
                  </section>
                )}
              </>
            ) : (
              <DataTable
                columns={[
                  "Event",
                  "Country",
                  "Date",
                  "Impact",
                  "Forecast",
                  "Previous",
                  "Actual",
                  "Evidence",
                ]}
                rows={filtered.map((row) => [
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
            )}
          </div>
        </div>
      )}
    </section>
  );
}
