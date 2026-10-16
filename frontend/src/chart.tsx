import { useEffect, useId, useMemo, useRef, useState } from "react";
import {
  calculate,
  defaultChart,
  type ChartSettings,
  type Drawing,
  type Anchor,
  type Series,
} from "./chart-model";
import { DrawingLayer } from "./chart-drawings";
import type { DrawingTool } from "./chart-editor";
import { DataInfo } from "./data-info";
import { price, ranges, type Candle, type Market, type Range } from "./bridge";
function endAt(candles: Candle[], timestamp: number): number {
  let lo = 0,
    hi = candles.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (candles[mid].time <= timestamp) lo = mid + 1;
    else hi = mid;
  }
  return Math.max(1, lo);
}
export type AnalysisProps = {
  settings: ChartSettings;
  drawings: Drawing[];
  tool: DrawingTool;
  drawingColor: string;
  drawingWidth: number;
  onDrawings: (drawings: Drawing[]) => void;
  disabled: boolean;
};
const defaults = defaultChart();
export function CandleChart({
  candles,
  analysis,
}: {
  candles: Candle[];
  analysis?: AnalysisProps;
}) {
  const useIdSafe = useId().replace(/[^a-zA-Z0-9]/g, "");
  const settings = analysis?.settings ?? defaults;
  const computed = useMemo(
    () => calculate(candles, settings),
    [candles, settings],
  );
  const [preview, setPreview] = useState<Drawing | null>(null),
    [selectedDrawing, setSelectedDrawing] = useState<string | null>(null);
  const gesture = useRef<{ drawing: Drawing; anchor: number } | null>(null);

  const [count, setCount] = useState(120),
    [endTime, setEndTime] = useState<number | null>(null),
    [hover, setHover] = useState<number | null>(null);
  const pan = useRef<{ x: number; end: number } | null>(null),
    scheduled = useRef<number | null>(null),
    svg = useRef<SVGSVGElement>(null);
  const zoom = useRef<(event: WheelEvent) => void>(() => {});
  const [size, setSize] = useState({ width: 1000, height: 400 });
  useEffect(() => {
    const plot = svg.current;
    if (!plot) return;
    const observer = new ResizeObserver(([entry]) => {
      const width = Math.max(200, Math.round(entry.contentRect.width));
      const height = Math.max(1, Math.round(entry.contentRect.height));
      setSize((previous) =>
        previous.width === width && previous.height === height
          ? previous
          : { width, height },
      );
    });
    observer.observe(plot);
    return () => observer.disconnect();
  }, [!!candles.length]);
  useEffect(() => {
    const plot = svg.current;
    if (!plot) return;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      zoom.current(event);
    };
    plot.addEventListener("wheel", wheel, { passive: false });
    return () => plot.removeEventListener("wheel", wheel);
  }, [!!candles.length]);
  useEffect(
    () => () => {
      if (scheduled.current !== null) cancelAnimationFrame(scheduled.current);
    },
    [],
  );
  const end = endTime === null ? candles.length : endAt(candles, endTime),
    start = Math.max(0, end - count),
    bars = candles.slice(start, end);
  if (!bars.length)
    return <div className="empty">Waiting for candle history</div>;
  const overlays: {
    values: Series;
    color: string;
    width: number;
    label: string;
  }[] = [];
  if (settings.sma.enabled)
    computed.sma.forEach((values, i) =>
      overlays.push({
        values,
        color: settings.sma.color,
        width: settings.sma.width,
        label: `SMA ${settings.sma.periods[i]}`,
      }),
    );
  if (settings.ema.enabled)
    overlays.push({
      values: computed.ema,
      color: settings.ema.color,
      width: settings.ema.width,
      label: `EMA ${settings.ema.periods[0]}`,
    });
  if (settings.bollinger.enabled)
    computed.bollinger.forEach((values, i) =>
      overlays.push({
        values,
        color: settings.bollinger.color,
        width: settings.bollinger.width,
        label: `BOLL ${["lower", "middle", "upper"][i]}`,
      }),
    );
  const overlayValues = overlays.flatMap((o) =>
    o.values.slice(start, end).filter((v): v is number => v !== null),
  );
  const low = Math.min(...bars.map((bar) => bar.low), ...overlayValues),
    high = Math.max(...bars.map((bar) => bar.high), ...overlayValues),
    spread = Math.max(high - low, Math.abs(high) * 0.002, 0.00000001),
    floor = low - spread * 0.08,
    ceiling = high + spread * 0.08;
  const left = 16,
    right = size.width - 76,
    oscillatorCount =
      Number(settings.rsi.enabled) + Number(settings.macd.enabled),
    oscillatorHeight = Math.min(84, Math.max(52, size.height * 0.17)),
    volumeHeight = settings.volume.enabled
      ? Math.min(48, Math.max(18, size.height * 0.12))
      : 0,
    bottom = Math.max(
      62,
      size.height - 38 - volumeHeight - oscillatorCount * oscillatorHeight,
    ),
    volumeBottom = bottom + volumeHeight + 8;
  const y = (value: number) =>
      18 + ((ceiling - value) / (ceiling - floor)) * (bottom - 18),
    step = (right - left) / bars.length;
  const maxVolume = Math.max(1, ...bars.map((bar) => bar.volume ?? 0));
  const indexAt = (clientX: number) => {
    const rect = svg.current?.getBoundingClientRect();
    return rect
      ? Math.max(
          0,
          Math.min(
            bars.length - 1,
            Math.floor(
              (((clientX - rect.left) / rect.width) * size.width - left) / step,
            ),
          ),
        )
      : 0;
  };
  const viewed = bars[Math.min(hover ?? bars.length - 1, bars.length - 1)];
  const times = bars.map((b) => b.time),
    drawings = analysis?.drawings ?? [];
  const displayed = preview
    ? [...drawings.filter((d) => d.id !== preview.id), preview]
    : drawings;
  const anchorAt = (clientX: number, clientY: number): Anchor => {
    const rect = svg.current?.getBoundingClientRect();
    const at = rect ? ((clientY - rect.top) / rect.height) * size.height : 18;
    return {
      time: bars[indexAt(clientX)].time,
      price:
        ceiling -
        ((Math.max(18, Math.min(bottom, at)) - 18) / (bottom - 18)) *
          (ceiling - floor),
    };
  };
  const updateGesture = (clientX: number, clientY: number) => {
    if (!gesture.current) return null;
    const current = gesture.current,
      anchor = anchorAt(clientX, clientY);
    const next: Drawing = {
      ...current.drawing,
      anchors: [...current.drawing.anchors],
    };
    next.anchors[current.anchor] = anchor;
    if (next.kind === "horizontal") next.anchors = [anchor, anchor];
    setPreview(next);
    return next;
  };
  const pathFor = (values: Series, at: (value: number) => number) => {
    let path = "",
      gap = true;
    values.slice(start, end).forEach((value, i) => {
      if (value === null) {
        gap = true;
        return;
      }
      path += `${gap ? "M" : "L"}${left + (i + 0.5) * step},${at(value)} `;
      gap = false;
    });
    return path;
  };
  const oscillator = (kind: "rsi" | "macd", index: number) => {
    const top = volumeBottom + index * oscillatorHeight + 6,
      base = top + oscillatorHeight - 14;
    const values =
      kind === "rsi"
        ? [computed.rsi]
        : [computed.macd, computed.signal, computed.histogram];
    const visible = values.flatMap((v) =>
      v.slice(start, end).filter((x): x is number => x !== null),
    );
    const min = kind === "rsi" ? 0 : Math.min(0, ...visible),
      max = kind === "rsi" ? 100 : Math.max(0, ...visible),
      span = Math.max(max - min, 1e-9);
    const at = (value: number) =>
      top + 12 + ((max - value) / span) * (base - top - 12);
    const read =
      values[0][start + Math.min(hover ?? bars.length - 1, bars.length - 1)];
    return (
      <g key={kind} aria-label={`${kind.toUpperCase()} indicator pane`}>
        <line x1={left} x2={right} y1={top} y2={top} className="grid-line" />
        <text className="axis" x={left} y={top + 10}>
          {kind.toUpperCase()}{" "}
          {read == null ? "Insufficient history" : price(read, 3)}
        </text>
        {kind === "rsi" ? (
          [30, 70].map((v) => (
            <line
              key={v}
              x1={left}
              x2={right}
              y1={at(v)}
              y2={at(v)}
              className="grid-line"
              strokeDasharray="3 3"
            />
          ))
        ) : (
          <>
            <line
              x1={left}
              x2={right}
              y1={at(0)}
              y2={at(0)}
              className="grid-line"
            />
            {computed.histogram
              .slice(start, end)
              .map((v, i) =>
                v === null ? null : (
                  <rect
                    key={i}
                    x={left + i * step + step * 0.2}
                    y={Math.min(at(v), at(0))}
                    width={step * 0.6}
                    height={Math.max(1, Math.abs(at(v) - at(0)))}
                    fill={v < 0 ? "#E02D48" : "#00A357"}
                  />
                ),
              )}
          </>
        )}
        {(kind === "rsi"
          ? [computed.rsi]
          : [computed.macd, computed.signal]
        ).map((v, i) => (
          <path
            key={i}
            d={pathFor(v, at)}
            fill="none"
            stroke={i === 0 ? settings[kind].color : "#EA670C"}
            strokeWidth={settings[kind].width}
          />
        ))}
        <text className="axis" x={right + 8} y={top + 20}>
          {price(max, 1)}
        </text>
        <text className="axis" x={right + 8} y={base}>
          {price(min, 1)}
        </text>
      </g>
    );
  };

  const setEnd = (value: number) => {
    const bounded = Math.max(
      Math.min(count, candles.length),
      Math.min(candles.length, Math.round(value)),
    );
    setEndTime(candles[Math.max(0, bounded - 1)]?.time ?? null);
  };
  zoom.current = (event: WheelEvent) => {
    event.preventDefault();
    const anchor = indexAt(event.clientX),
      next = Math.max(
        20,
        Math.min(800, Math.round(count * (event.deltaY > 0 ? 1.2 : 0.8))),
      );
    const fraction = anchor / Math.max(1, bars.length);
    const nextEnd = Math.max(
      Math.min(next, candles.length),
      Math.min(
        candles.length,
        Math.round(start + anchor + next * (1 - fraction)),
      ),
    );
    setCount(next);
    if (endTime !== null || nextEnd < candles.length)
      setEndTime(candles[nextEnd - 1]?.time ?? null);
    setHover(null);
  };
  return (
    <div className="interactive-chart">
      <div className="chart-readout" aria-live="off">
        <time>
          {new Date(viewed.time * 1000)
            .toISOString()
            .slice(0, 16)
            .replace("T", " ")}{" "}
          UTC
        </time>
        <span>O {price(viewed.open)}</span>
        <span>H {price(viewed.high)}</span>
        <span>L {price(viewed.low)}</span>
        <span>C {price(viewed.close)}</span>
        <span>
          V {viewed.volume == null ? "Unavailable" : price(viewed.volume, 0)}
        </span>
        {overlays.map((o, i) => (
          <span key={i} style={{ color: o.color }}>
            {o.label}{" "}
            {o.values[
              start + Math.min(hover ?? bars.length - 1, bars.length - 1)
            ] == null
              ? "Insufficient history"
              : price(
                  o.values[
                    start + Math.min(hover ?? bars.length - 1, bars.length - 1)
                  ],
                  3,
                )}
          </span>
        ))}
      </div>
      <div className="chart-controls">
        <span>
          {bars.length} candles ·{" "}
          {endTime === null ? "Following latest" : "Historical view"}
        </span>
        <button
          onClick={() => {
            setCount(120);
            setEndTime(null);
            setHover(null);
          }}
        >
          Reset view
        </button>
        <button
          aria-pressed={endTime === null}
          onClick={() => {
            setEndTime(null);
            setHover(null);
          }}
        >
          Follow latest
        </button>
        {analysis && (
          <button
            disabled={
              analysis.disabled ||
              !drawings.some((d) => d.id === selectedDrawing)
            }
            onClick={() => {
              analysis.onDrawings(
                drawings.filter((d) => d.id !== selectedDrawing),
              );
              setSelectedDrawing(null);
            }}
          >
            Delete selected drawing
          </button>
        )}
      </div>
      <svg
        ref={svg}
        className="chart"
        viewBox={`0 0 ${size.width} ${size.height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={
          settings.style === "candles"
            ? "OHLC candlestick chart"
            : "Closing-price line chart"
        }
        tabIndex={0}
        onPointerDown={(event) => {
          if (event.button !== 0) return;
          event.currentTarget.focus();
          if (analysis && !analysis.disabled && analysis.tool !== "select") {
            if (drawings.length >= 100) return;
            const rect = event.currentTarget.getBoundingClientRect();
            if (
              ((event.clientY - rect.top) / rect.height) * size.height >
              bottom
            )
              return;
            const anchor = anchorAt(event.clientX, event.clientY);
            const drawing: Drawing = {
              id: crypto.randomUUID(),
              kind: analysis.tool,
              anchors: [anchor, anchor],
              color: analysis.drawingColor,
              width: analysis.drawingWidth,
            };
            gesture.current = { drawing, anchor: 1 };
            setPreview(drawing);
            setSelectedDrawing(drawing.id);
          } else {
            setSelectedDrawing(null);
            pan.current = { x: event.clientX, end };
          }
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          const x = event.clientX,
            clientY = event.clientY;
          if (scheduled.current !== null)
            cancelAnimationFrame(scheduled.current);
          scheduled.current = requestAnimationFrame(() => {
            scheduled.current = null;
            setHover(indexAt(x));
            if (gesture.current) {
              updateGesture(x, clientY);
              return;
            }
            if (pan.current && svg.current) {
              const rect = svg.current.getBoundingClientRect();
              setEnd(
                pan.current.end -
                  (((x - pan.current.x) / rect.width) * size.width) / step,
              );
            }
          });
        }}
        onPointerUp={(event) => {
          if (scheduled.current !== null) {
            cancelAnimationFrame(scheduled.current);
            scheduled.current = null;
          }
          if (gesture.current && analysis) {
            const next = updateGesture(event.clientX, event.clientY);
            gesture.current = null;
            setPreview(null);
            if (next)
              analysis.onDrawings([
                ...drawings.filter((d) => d.id !== next.id),
                next,
              ]);
          }
          pan.current = null;
          if (event.currentTarget.hasPointerCapture(event.pointerId))
            event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onPointerCancel={() => {
          gesture.current = null;
          setPreview(null);
          pan.current = null;
        }}
        onLostPointerCapture={() => {
          gesture.current = null;
          setPreview(null);
          pan.current = null;
        }}
        onPointerLeave={() => {
          if (!pan.current) setHover(null);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            gesture.current = null;
            setPreview(null);
            setSelectedDrawing(null);
            event.stopPropagation();
            return;
          }
          if (
            event.key === "Delete" &&
            selectedDrawing &&
            analysis &&
            !analysis.disabled
          ) {
            event.preventDefault();
            event.stopPropagation();
            analysis.onDrawings(
              drawings.filter((d) => d.id !== selectedDrawing),
            );
            setSelectedDrawing(null);
            return;
          }

          if (
            !["ArrowLeft", "ArrowRight", "Home", "End", "+", "-"].includes(
              event.key,
            )
          )
            return;
          event.preventDefault();
          event.stopPropagation();
          if (event.key === "Home") setEnd(count);
          else if (event.key === "End") setEndTime(null);
          else if (event.key === "+" || event.key === "-")
            setCount((value) =>
              Math.max(
                20,
                Math.min(
                  800,
                  Math.round(value * (event.key === "+" ? 0.8 : 1.2)),
                ),
              ),
            );
          else
            setHover((value) =>
              Math.max(
                0,
                Math.min(
                  bars.length - 1,
                  (value ?? bars.length - 1) +
                    (event.key === "ArrowLeft" ? -1 : 1),
                ),
              ),
            );
        }}
      >
        <title>
          Unadjusted OHLC history. Drag to pan, scroll to zoom, arrow keys move
          crosshair.
        </title>
        {[0, 1, 2, 3, 4].map((index) => {
          const value = ceiling - ((ceiling - floor) * index) / 4,
            at = y(value);
          return (
            <g key={index}>
              <line
                x1={left}
                x2={right}
                y1={at}
                y2={at}
                className="grid-line"
              />
              <text x={right + 10} y={at + 4} className="axis">
                {price(value)}
              </text>
            </g>
          );
        })}
        {bars.map((bar, index) => {
          const x = left + index * step + step / 2,
            top = y(Math.max(bar.open, bar.close)),
            height = Math.max(Math.abs(y(bar.open) - y(bar.close)), 1);
          return (
            <g
              key={bar.time}
              className={bar.close >= bar.open ? "candle-up" : "candle-down"}
            >
              <title>
                {new Date(bar.time * 1000).toISOString()} · O {price(bar.open)}{" "}
                H {price(bar.high)} L {price(bar.low)} C {price(bar.close)}
              </title>
              {settings.style === "candles" && (
                <>
                  <line x1={x} x2={x} y1={y(bar.high)} y2={y(bar.low)} />
                  <rect
                    x={x - step * 0.29}
                    y={top}
                    width={step * 0.58}
                    height={height}
                  />
                </>
              )}
              {settings.volume.enabled && bar.volume != null && (
                <rect
                  className="volume-bar"
                  x={x - step * 0.29}
                  y={volumeBottom - (bar.volume / maxVolume) * volumeHeight}
                  width={step * 0.58}
                  height={(bar.volume / maxVolume) * volumeHeight}
                  style={{
                    fill: settings.volume.color,
                    strokeWidth: settings.volume.width,
                  }}
                />
              )}
            </g>
          );
        })}
        <defs>
          <clipPath id={`price-${useIdSafe}`}>
            <rect
              x={left}
              y="18"
              width={Math.max(1, right - left)}
              height={Math.max(1, bottom - 18)}
            />
          </clipPath>
        </defs>
        <g clipPath={`url(#price-${useIdSafe})`}>
          {settings.style === "line" && (
            <path
              d={pathFor(
                candles.map((c) => c.close),
                y,
              )}
              fill="none"
              stroke="#4E7FCE"
              strokeWidth="1.5"
            />
          )}
          {overlays.map((o, i) => (
            <path
              key={i}
              d={pathFor(o.values, y)}
              fill="none"
              stroke={o.color}
              strokeWidth={o.width}
            />
          ))}
          <DrawingLayer
            interactive={
              !!analysis && analysis.tool === "select" && !analysis.disabled
            }
            drawings={displayed}
            times={times}
            left={left}
            right={right}
            step={step}
            y={y}
            selected={selectedDrawing}
            onSelect={setSelectedDrawing}
            onHandle={(event, drawing, index) => {
              event.preventDefault();
              event.stopPropagation();
              if (!analysis || analysis.disabled) return;
              gesture.current = { drawing, anchor: index };
              setPreview(drawing);
              svg.current?.focus();
              svg.current?.setPointerCapture(event.pointerId);
            }}
          />
        </g>
        {settings.rsi.enabled && oscillator("rsi", 0)}
        {settings.macd.enabled &&
          oscillator("macd", settings.rsi.enabled ? 1 : 0)}
        {hover !== null && (
          <g className="crosshair">
            <line
              x1={left + Math.min(hover, bars.length - 1) * step + step / 2}
              x2={left + Math.min(hover, bars.length - 1) * step + step / 2}
              y1="18"
              y2={size.height - 24}
            />
            <line
              x1={left}
              x2={right}
              y1={y(viewed.close)}
              y2={y(viewed.close)}
            />
          </g>
        )}
        {[0, Math.floor(bars.length / 2), bars.length - 1]
          .filter((value, index, all) => all.indexOf(value) === index)
          .map((index) => (
            <text
              className="axis"
              key={index}
              x={left + index * step + step / 2}
              y={size.height - 5}
              textAnchor={index === bars.length - 1 ? "end" : "start"}
            >
              {new Date(bars[index].time * 1000).toLocaleDateString("en", {
                month: "short",
                day: "numeric",
                timeZone: "UTC",
              })}
            </text>
          ))}
      </svg>
    </div>
  );
}
export function MarketChart({
  market,
  range,
  identity,
  currency,
  onRange,
  analysis,
}: {
  analysis?: AnalysisProps;
  market: Market<Candle[]>;
  range: Range;
  identity: string;
  currency: string | null;
  onRange: (range: Range) => void;
}) {
  return (
    <section className="panel history">
      <div className="panel-head">
        <h3>
          Price history <span>OHLC</span>
        </h3>
        <div className="ranges" aria-label="History range">
          {ranges.map((value) => (
            <button
              key={value}
              className={value === range ? "selected" : ""}
              aria-pressed={value === range}
              onClick={() => onRange(value)}
            >
              {value}
            </button>
          ))}
        </div>
      </div>
      <div className="chart-meta">
        <span>
          {market.interval ?? "Awaiting interval"} ·{" "}
          {currency ?? "Currency unavailable"}
        </span>
        <span className={market.stale ? "negative" : ""}>
          {market.stale ? "STALE DATA" : "UNADJUSTED"}
        </span>
      </div>
      {market.data?.length ? (
        <CandleChart
          key={`${identity}:${range}`}
          candles={market.data}
          analysis={analysis}
        />
      ) : (
        <div className="empty">{market.error ?? "No history available"}</div>
      )}
      <DataInfo market={market} />
    </section>
  );
}
