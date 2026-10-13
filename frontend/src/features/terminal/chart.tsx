import { useEffect, useRef, useState } from "react";
import {
  createChart,
  CandlestickSeries,
  BarSeries,
  LineSeries,
  AreaSeries,
  HistogramSeries,
  IChartApi,
  ISeriesApi,
  SeriesType,
  UTCTimestamp,
} from "lightweight-charts";
import {
  Candle,
  Point,
  sma,
  ema,
  rsi,
  bollinger,
  vwap,
  macd,
} from "./indicators";
import { useMarket, display } from "./market";
import { Instrument } from "./state";
import { useWidgetState } from "./settings";
import { MarketStatus } from "./widgets";
const ranges = ["1D", "5D", "1M", "6M", "YTD", "1Y", "5Y", "MAX"];
const indicators = [
  "SMA20",
  "SMA50",
  "SMA200",
  "EMA20",
  "VWAP",
  "BOLL",
  "RSI",
  "MACD",
];
export function ChartWidget({
  instrument,
  active,
}: {
  instrument: Instrument;
  active: boolean;
}) {
  const [savedRange, setRange] = useWidgetState("range", "6M"),
    [savedKind, setKind] = useWidgetState("kind", "Candles"),
    [savedIndicators, setEnabled] = useWidgetState<string[]>("indicators", []),
    [legend, setLegend] = useState<Record<string, number>>({});
  const range = ranges.includes(savedRange) ? savedRange : "6M";
  const kind = ["Candles", "Bars", "Line", "Area"].includes(savedKind)
    ? savedKind
    : "Candles";
  const enabled = savedIndicators.filter((value) => indicators.includes(value));
  const data = useMarket<Candle[]>(
    `/market/ohlcv?instrument=${encodeURIComponent(instrument.id)}&range=${range}`,
    range === "1D" || range === "5D" ? 60000 : 300000,
    active,
  );
  const element = useRef<HTMLDivElement>(null),
    chart = useRef<IChartApi | null>(null),
    series = useRef<Record<string, ISeriesApi<SeriesType>>>({}),
    previousRange = useRef("");
  const signature = enabled.join(",");
  useEffect(() => {
    if (!element.current) return;
    const api = createChart(element.current, {
      autoSize: true,
      layout: {
        background: { color: "#0c0c0c" },
        textColor: "#a7a7a7",
        attributionLogo: true,
      },
      grid: {
        vertLines: { color: "#1c1c1c" },
        horzLines: { color: "#1c1c1c" },
      },
      timeScale: { timeVisible: true },
      rightPriceScale: { borderColor: "#333" },
    });
    chart.current = api;
    series.current = {};
    previousRange.current = "";
    const defs = {
      Candles: CandlestickSeries,
      Bars: BarSeries,
      Line: LineSeries,
      Area: AreaSeries,
    };
    series.current.Price = api.addSeries(defs[kind as keyof typeof defs], {
      color: "#ff9900",
      upColor: "#00c853",
      downColor: "#ff5252",
      wickUpColor: "#00c853",
      wickDownColor: "#ff5252",
      borderVisible: false,
    });
    series.current.Volume = api.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
      color: "#305646",
    });
    api
      .priceScale("volume")
      .applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    let pane = 1;
    enabled.forEach((name, index) => {
      const color = ["#ff9900", "#82b1ff", "#ce93d8", "#80cbc4"][index % 4];
      if (name === "BOLL") {
        ["Upper", "Middle", "Lower"].forEach((part) => {
          series.current["BOLL " + part] = api.addSeries(LineSeries, {
            color,
            lineWidth: 1,
            priceLineVisible: false,
            lastValueVisible: false,
          });
        });
      } else if (name === "MACD") {
        ["MACD", "Signal", "Histogram"].forEach((part) => {
          series.current[part] =
            part === "Histogram"
              ? api.addSeries(HistogramSeries, { color: "#4585af" }, pane)
              : api.addSeries(
                  LineSeries,
                  {
                    color: part === "MACD" ? "#82b1ff" : "#ff9900",
                    lineWidth: 1,
                  },
                  pane,
                );
        });
        pane++;
      } else
        series.current[name] = api.addSeries(
          LineSeries,
          { color, lineWidth: 1, priceLineVisible: false },
          name === "RSI" ? pane++ : 0,
        );
    });
    const hover = (
      event: Parameters<IChartApi["subscribeCrosshairMove"]>[0] extends (
        p: infer P,
      ) => void
        ? P
        : never,
    ) => {
      const values: Record<string, number> = {};
      Object.entries(series.current).forEach(([name, s]) => {
        const row = event.seriesData.get(s);
        if (!row) return;
        if ("close" in row) {
          values.Open = row.open;
          values.High = row.high;
          values.Low = row.low;
          values.Close = row.close;
        } else if ("value" in row) values[name] = row.value;
      });
      setLegend(values);
    };
    api.subscribeCrosshairMove(hover);
    return () => {
      api.unsubscribeCrosshairMove(hover);
      api.remove();
      chart.current = null;
      series.current = {};
    };
  }, [kind, signature]);
  useEffect(() => {
    const candles = data?.data || [];
    if (!chart.current || !candles.length) return;
    const typed = candles.map((c) => ({ ...c, time: c.time as UTCTimestamp }));
    series.current.Price.setData(
      kind === "Line" || kind === "Area"
        ? typed.map((c) => ({ time: c.time, value: c.close }))
        : typed,
    );
    series.current.Volume.setData(
      typed
        .filter((c) => c.volume !== null)
        .map((c) => ({
          time: c.time,
          value: c.volume as number,
          color: c.close >= c.open ? "#23553d" : "#66332d",
        })),
    );
    const points: Record<string, Point[]> = {};
    enabled.forEach((name) => {
      if (name.startsWith("SMA"))
        points[name] = sma(candles, Number(name.slice(3)));
      else if (name === "EMA20") points[name] = ema(candles, 20);
      else if (name === "VWAP")
        points[name] = vwap(candles, range === "1D" || range === "5D");
      else if (name === "RSI") points[name] = rsi(candles);
      else if (name === "BOLL") {
        const b = bollinger(candles);
        points["BOLL Upper"] = b.upper;
        points["BOLL Middle"] = b.middle;
        points["BOLL Lower"] = b.lower;
      } else if (name === "MACD") {
        const m = macd(candles);
        points.MACD = m.line;
        points.Signal = m.signal;
        points.Histogram = m.histogram;
      }
    });
    Object.entries(points).forEach(([name, values]) =>
      series.current[name]?.setData(
        values.map((p) => ({ ...p, time: p.time as UTCTimestamp })),
      ),
    );
    if (previousRange.current !== range + instrument.id) {
      chart.current.timeScale().fitContent();
      previousRange.current = range + instrument.id;
      setLegend({
        Close: candles.at(-1)!.close,
        ...(candles.at(-1)!.volume !== null
          ? { Volume: candles.at(-1)!.volume! }
          : {}),
      });
    }
  }, [data, kind, signature, range, instrument.id]);
  return (
    <>
      <div className="chart-controls">
        <select
          aria-label="Chart type"
          value={kind}
          onChange={(e) => setKind(e.target.value)}
        >
          {["Candles", "Bars", "Line", "Area"].map((v) => (
            <option key={v}>{v}</option>
          ))}
        </select>
        {ranges.map((v) => (
          <button
            key={v}
            className={range === v ? "selected" : ""}
            onClick={() => setRange(v)}
          >
            {v}
          </button>
        ))}
      </div>
      <div className="chart-indicators">
        {indicators.map((v) => (
          <label key={v}>
            <input
              type="checkbox"
              checked={enabled.includes(v)}
              onChange={() =>
                setEnabled((old) =>
                  old.includes(v) ? old.filter((x) => x !== v) : [...old, v],
                )
              }
            />
            {v}
          </label>
        ))}
      </div>
      <div className="chart-legend">
        {Object.entries(legend).map(([name, value]) => (
          <span key={name}>
            {name} <b>{display(value, 4)}</b>
          </span>
        ))}
      </div>
      <div ref={element} className="market-chart" />
      <small>
        VWAP: {range === "1D" || range === "5D" ? "session" : "range-anchored"}{" "}
        · {data?.currency || "currency unavailable"}
      </small>
      <MarketStatus value={data} />
    </>
  );
}
