import { useWidgetState } from "./settings";
import { Treemap, ResponsiveContainer } from "recharts";
import { useMarket } from "./market";
import { MarketRow, MarketStatus } from "./widgets";
import { Instrument } from "./state";
export function HeatmapWidget({
  active,
  onPick,
}: {
  active: boolean;
  onPick: (i: Instrument) => void;
}) {
  const [market, setMarket] = useWidgetState("market", "MY"),
    [currency, setCurrency] = useWidgetState("currency", "");
  const data = useMarket<MarketRow[]>(
    `/market/screener?market=${market}&sort=market_cap&descending=true&offset=0&limit=250`,
    60000,
    active,
  );
  const rows = data?.data || [];
  const currencies = [...new Set(rows.map((r) => r.currency).filter(Boolean))];
  const chosen = currencies.includes(currency) ? currency : currencies[0];
  const valid = rows.filter(
    (r) => r.currency === chosen && (r.market_cap || 0) > 0,
  );
  const sectors = [
    ...new Set(valid.map((r) => r.sector || "Unclassified")),
  ].map((sector) => ({
    name: sector,
    children: valid
      .filter((r) => (r.sector || "Unclassified") === sector)
      .map((r) => ({ ...r, name: r.instrument.symbol, size: r.market_cap })),
  }));
  return (
    <>
      <div className="inline">
        <select
          aria-label="Heatmap market"
          value={market}
          onChange={(e) => setMarket(e.target.value)}
        >
          <option value="MY">Malaysia</option>
          <option value="US">US</option>
        </select>
        <select
          aria-label="Heatmap currency"
          value={chosen || ""}
          onChange={(e) => setCurrency(e.target.value)}
        >
          {currencies.map((c) => (
            <option key={c!}>{c}</option>
          ))}
        </select>
      </div>
      <div className="heatmap-chart">
        <ResponsiveContainer width="100%" height={300}>
          <Treemap
            data={sectors}
            dataKey="size"
            nameKey="name"
            isAnimationActive={false}
            content={(props) => {
              const row = props as unknown as {
                x: number;
                y: number;
                width: number;
                height: number;
                depth: number;
                name: string;
                instrument?: Instrument;
                change_percent?: number;
              };
              const change = row.change_percent || 0;
              return (
                <g
                  onClick={() => row.instrument && onPick(row.instrument)}
                  role={row.instrument ? "button" : undefined}
                  tabIndex={row.instrument ? 0 : undefined}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && row.instrument)
                      onPick(row.instrument);
                  }}
                >
                  <rect
                    x={row.x}
                    y={row.y}
                    width={row.width}
                    height={row.height}
                    fill={
                      row.depth < 2
                        ? "#151515"
                        : change >= 0
                          ? `rgb(20,${Math.min(135, 45 + change * 15)},45)`
                          : `rgb(${Math.min(150, 60 - change * 15)},30,30)`
                    }
                    stroke="#0a0a0a"
                  />
                  {row.width > 45 && row.height > 24 && (
                    <text
                      x={row.x + 5}
                      y={row.y + 16}
                      fill="#ddd"
                      fontSize={11}
                    >
                      {row.name}
                    </text>
                  )}
                </g>
              );
            }}
          />
        </ResponsiveContainer>
      </div>
      <p className="muted">
        Largest 250 provider matches · {rows.length - valid.length} excluded for
        missing cap/currency or another currency · Universe total:{" "}
        {String(data?.total ?? "unknown")}
      </p>
      <MarketStatus value={data} />
    </>
  );
}
