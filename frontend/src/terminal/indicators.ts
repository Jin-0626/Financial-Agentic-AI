export type Candle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
  session?: string;
};
export type Point = { time: number; value: number };
function point(c: Candle, value: number): Point {
  return { time: c.time, value };
}
export function sma(c: Candle[], window: number): Point[] {
  if (c.length < window) return [];
  let total = 0;
  const out: Point[] = [];
  for (let i = 0; i < c.length; i++) {
    total += c[i].close;
    if (i >= window) total -= c[i - window].close;
    if (i >= window - 1) out.push(point(c[i], total / window));
  }
  return out;
}
export function ema(c: Candle[], window: number): Point[] {
  if (c.length < window) return [];
  let average =
    c.slice(0, window).reduce((sum, v) => sum + v.close, 0) / window;
  const out = [point(c[window - 1], average)];
  const alpha = 2 / (window + 1);
  for (let i = window; i < c.length; i++) {
    average = alpha * c[i].close + (1 - alpha) * average;
    out.push(point(c[i], average));
  }
  return out;
}
export function rsi(c: Candle[], window = 14): Point[] {
  if (c.length <= window) return [];
  let gain = 0,
    loss = 0;
  for (let i = 1; i <= window; i++) {
    const delta = c[i].close - c[i - 1].close;
    gain += Math.max(delta, 0) / window;
    loss += Math.max(-delta, 0) / window;
  }
  const value = () =>
    gain === 0 && loss === 0
      ? 50
      : loss === 0
        ? 100
        : 100 - 100 / (1 + gain / loss);
  const out = [point(c[window], value())];
  for (let i = window + 1; i < c.length; i++) {
    const delta = c[i].close - c[i - 1].close;
    gain = (gain * (window - 1) + Math.max(delta, 0)) / window;
    loss = (loss * (window - 1) + Math.max(-delta, 0)) / window;
    out.push(point(c[i], value()));
  }
  return out;
}
export function bollinger(c: Candle[], window = 20) {
  const mean = sma(c, window);
  return {
    middle: mean,
    upper: mean.map((p, i) => ({
      ...p,
      value:
        p.value +
        2 *
          Math.sqrt(
            c
              .slice(i, i + window)
              .reduce((sum, v) => sum + (v.close - p.value) ** 2, 0) / window,
          ),
    })),
    lower: mean.map((p, i) => ({
      ...p,
      value:
        p.value -
        2 *
          Math.sqrt(
            c
              .slice(i, i + window)
              .reduce((sum, v) => sum + (v.close - p.value) ** 2, 0) / window,
          ),
    })),
  };
}
export function vwap(c: Candle[], session: boolean): Point[] {
  let total = 0,
    volume = 0,
    last = "";
  const out: Point[] = [];
  for (const row of c) {
    const current =
      row.session || new Date(row.time * 1000).toISOString().slice(0, 10);
    if (session && current !== last) {
      total = 0;
      volume = 0;
    }
    last = current;
    if (row.volume !== null && row.volume > 0) {
      total += ((row.high + row.low + row.close) / 3) * row.volume;
      volume += row.volume;
    }
    if (volume > 0) out.push(point(row, total / volume));
  }
  return out;
}
export function macd(c: Candle[]) {
  const fast = new Map(ema(c, 12).map((p) => [p.time, p.value]));
  const slow = ema(c, 26);
  const line = slow.map((p) => ({
    ...p,
    value: (fast.get(p.time) || 0) - p.value,
  }));
  const fake = line.map((p) => ({
    time: p.time,
    open: p.value,
    close: p.value,
    high: p.value,
    low: p.value,
    volume: null,
  }));
  const signal = ema(fake, 9);
  const byTime = new Map(signal.map((p) => [p.time, p.value]));
  return {
    line,
    signal,
    histogram: line
      .filter((p) => byTime.has(p.time))
      .map((p) => ({ ...p, value: p.value - (byTime.get(p.time) || 0) })),
  };
}
