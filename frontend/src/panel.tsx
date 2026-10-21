import { StockLabel } from "./stock-label";
import { useState, type ReactNode } from "react";
import { DEFAULT_LAYOUT_NAME, type PanelSpec, type Snapshot, type Range } from "./bridge";
import { ChartEditor } from "./chart-editor";
import { MarketChart } from "./chart";
import { FeatureScreen, MarketResource, useResource } from "./resources";
export function Panel({
  id,
  spec,
  snapshot,
  onChange,
  watchlist,
  owner,
  onGlobalRange,
  editable = true,
  onSelect,
}: {
  id: string;
  spec: PanelSpec;
  snapshot: Snapshot;
  onChange: (spec: PanelSpec) => void;
  watchlist: ReactNode;
  owner: string;
  onGlobalRange: (range: Range) => void;
  editable?: boolean;
  onSelect: (id: string) => void;
}) {
  const selected =
    spec.binding.type === "pinned"
      ? spec.binding.instrument
      : snapshot.preferences.selected;
  const range = spec.range ?? snapshot.preferences.range;
  const history = useResource(
    spec.kind === "chart" && selected
      ? { type: "history", instrument: selected, range }
      : null,
    true,
    `${owner}:history`,
  );
  const quote = useResource(
    spec.kind === "chart" && selected
      ? { type: "quote", instrument: selected }
      : null,
    true,
    `${owner}:quote`,
  );
  const [pin, setPin] = useState(
    spec.binding.type === "pinned" ? spec.binding.instrument : (selected ?? ""),
  );
  const instrumentPanel = spec.kind === "chart" || spec.kind === "news";
  const selectedQuote =
    quote.payload?.type === "quote" ? quote.payload.market : null;
  let content: ReactNode;
  switch (spec.kind) {
    case "chart": {
      const same =
        selected === snapshot.preferences.selected &&
        range === snapshot.preferences.range;
      const market =
        history.payload?.type === "history"
          ? history.payload.market
          : same
            ? snapshot.candles
            : null;
      content = market ? (
        <ChartEditor
          key={`${id}:${selected}`}
          spec={spec}
          layout={
            snapshot.preferences.workspace?.active_layout ??
            DEFAULT_LAYOUT_NAME
          }
          panel={id}
          instrument={selected ?? "none"}
        >
          {(analysis) => (
            <MarketChart
              analysis={analysis}
              market={market}
              range={range}
              identity={selected ?? "none"}
              name={selectedQuote?.data?.instrument.name}
              currency={
                selectedQuote?.data?.currency ??
                (selected
                  ? (snapshot.quotes[selected]?.data?.currency ?? null)
                  : null)
              }
              onRange={(range) =>
                spec.binding.type === "linked" && spec.range == null
                  ? onGlobalRange(range)
                  : onChange({ ...spec, range })
              }
            />
          )}
        </ChartEditor>
      ) : (
        <div className="empty">{history.error ?? "Loading history…"}</div>
      );
      break;
    }
    case "watchlist":
      content = watchlist;
      break;
    case "news":
      content = (
        <MarketResource
          owner={`${owner}:news`}
          request={{ type: "news", instrument: selected }}
          onSelect={onSelect}
        />
      );
      break;
    case "macro":
    case "calendar":
      content = (
        <MarketResource
          owner={`${owner}:${spec.kind}`}
          request={{ type: spec.kind }}
          onSelect={onSelect}
        />
      );
      break;
    case "screener":
      content = (
        <MarketResource
          owner={`${owner}:screener`}
          request={{
            type: "screener",
            market: "MY",
            sector: null,
            sort: "market_cap",
            descending: true,
            offset: 0,
          }}
          onSelect={onSelect}
        />
      );
      break;
    case "portfolio":
    case "research":
      content = (
        <FeatureScreen
          screen={spec.kind}
          selected={selected}
          mode={snapshot.mode}
          onSelect={onSelect}
        />
      );
      break;
  }
  return (
    <div className="panel-instance" data-panel-id={id}>
      {instrumentPanel && editable && (
        <div className="panel-binding">
          <span>
            {spec.binding.type === "pinned" ? "Pinned" : "Linked"} ·{" "}
            {selected ? (
              <StockLabel
                code={selected}
                name={selectedQuote?.data?.instrument.name}
              />
            ) : (
              "No instrument"
            )}
          </span>
          <details>
            <summary>Symbol</summary>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                const value = pin.trim();
                if (value)
                  onChange({
                    ...spec,
                    binding: {
                      type: "pinned",
                      instrument: value.includes(":")
                        ? value
                        : `yahoo:${value.toUpperCase()}`,
                    },
                  });
                event.currentTarget.closest("details")?.removeAttribute("open");
              }}
            >
              {spec.kind === "chart" && (
                <label className="chart-range-mode">
                  <input
                    type="checkbox"
                    checked={spec.range != null}
                    onChange={(e) =>
                      onChange({
                        ...spec,
                        range: e.target.checked ? range : null,
                      })
                    }
                  />
                  Independent range
                </label>
              )}
              <input
                aria-label={`Pin instrument ${id}`}
                value={pin}
                maxLength={128}
                onChange={(event) => setPin(event.target.value)}
              />
              <button>Pin symbol</button>
              <button
                type="button"
                onClick={() =>
                  onChange({
                    ...spec,
                    binding: { type: "linked" },
                    range: null,
                  })
                }
              >
                Follow active instrument
              </button>
            </form>
          </details>
        </div>
      )}
      {history.error && spec.kind === "chart" && history.payload && (
        <p role="alert" className="negative">
          {history.error}
        </p>
      )}
      {content}
    </div>
  );
}
