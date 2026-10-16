import type { Candle } from "./bridge";
export type Indicator = {
  enabled: boolean;
  periods: number[];
  color: string;
  width: number;
};
export type ChartSettings = {
  style: "candles" | "line";
  sma: Indicator;
  ema: Indicator;
  bollinger: Indicator;
  rsi: Indicator;
  macd: Indicator;
  volume: Indicator;
};
export type IndicatorKey = Exclude<keyof ChartSettings, "style">;
export type Anchor = { time: number; price: number };
export type Drawing = {
  id: string;
  kind: "trend" | "horizontal" | "rectangle" | "fibonacci";
  anchors: [Anchor, Anchor];
  color: string;
  width: number;
};
export const defaultChart = (): ChartSettings => {
  const indicator = (
    periods: number[],
    color: string,
    enabled = false,
  ): Indicator => ({ enabled, periods, color, width: 1 });
  return {
    style: "candles",
    sma: indicator([5, 10, 20], "#EA670C"),
    ema: indicator([20], "#4E7FCE"),
    bollinger: indicator([20], "#A78BFA"),
    rsi: indicator([14], "#E8B44C"),
    macd: indicator([12, 26, 9], "#4E7FCE"),
    volume: indicator([], "#00A357", true),
  };
};
export type Series = (number | null)[];
export function sma(values: Series, period: number): Series {
  let sum = 0,
    valid = 0;
  return values.map((v, i) => {
    if (v !== null) {
      sum += v;
      valid++;
    }
    if (i >= period) {
      const old = values[i - period];
      if (old !== null) {
        sum -= old;
        valid--;
      }
    }
    return i >= period - 1 && valid === period ? sum / period : null;
  });
}
export function ema(values: Series, period: number): Series {
  let seed: number[] = [],
    previous: number | null = null;
  const alpha = 2 / (period + 1);
  return values.map((v) => {
    if (v === null) {
      seed = [];
      previous = null;
      return null;
    }
    if (previous === null) {
      seed.push(v);
      if (seed.length < period) return null;
      previous = seed.reduce((a, b) => a + b, 0) / period;
    } else previous = alpha * v + (1 - alpha) * previous;
    return previous;
  });
}
export function rsi(values: number[], period: number): Series {
  let gain = 0,
    loss = 0;
  return values.map((v, i) => {
    if (i === 0) return null;
    const diff = v - values[i - 1],
      g = Math.max(diff, 0),
      l = Math.max(-diff, 0);
    if (i <= period) {
      gain += g / period;
      loss += l / period;
    } else {
      gain = (gain * (period - 1) + g) / period;
      loss = (loss * (period - 1) + l) / period;
    }
    return i < period
      ? null
      : gain === 0 && loss === 0
        ? 50
        : loss === 0
          ? 100
          : 100 - 100 / (1 + gain / loss);
  });
}
export function calculate(candles: Candle[], settings: ChartSettings) {
  const values = candles.map((c) => c.close),
    middle = sma(values, settings.bollinger.periods[0]),
    period = settings.bollinger.periods[0];
  const band = (direction: number) =>
    middle.map((mean, i) =>
      mean === null
        ? null
        : mean +
          direction *
            2 *
            Math.sqrt(
              values
                .slice(i - period + 1, i + 1)
                .reduce((s, v) => s + (v - mean) ** 2, 0) / period,
            ),
    );
  const fast = ema(values, settings.macd.periods[0]),
    slow = ema(values, settings.macd.periods[1]),
    macd = fast.map((v, i) =>
      v === null || slow[i] === null ? null : v - slow[i]!,
    ),
    signal = ema(macd, settings.macd.periods[2]);
  return {
    sma: settings.sma.periods.map((p) => sma(values, p)),
    ema: ema(values, settings.ema.periods[0]),
    bollinger: [band(-1), middle, band(1)],
    rsi: rsi(values, settings.rsi.periods[0]),
    macd,
    signal,
    histogram: macd.map((v, i) =>
      v === null || signal[i] === null ? null : v - signal[i]!,
    ),
  };
}
export function validSettings(s: ChartSettings): boolean {
  return (
    (["sma", "ema", "bollinger", "rsi", "macd", "volume"] as const).every(
      (k) =>
        s[k].periods.length === defaultChart()[k].periods.length &&
        s[k].periods.every((p) => Number.isInteger(p) && p >= 2 && p <= 250) &&
        /^#[a-f\d]{6}$/i.test(s[k].color) &&
        Number.isInteger(s[k].width) &&
        s[k].width >= 1 &&
        s[k].width <= 4,
    ) && s.macd.periods[0] < s.macd.periods[1]
  );
}
export function anchorToPoint(
  anchor: Anchor,
  times: number[],
  left: number,
  step: number,
  y: (price: number) => number,
): [number, number] {
  let lo = 0,
    hi = times.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (times[mid] < anchor.time) lo = mid + 1;
    else hi = mid;
  }
  let index = lo;
  if (times.length > 1 && anchor.time !== times[lo]) {
    const a = anchor.time < times[0] ? 0 : Math.max(0, lo - 1),
      b = Math.min(times.length - 1, a + 1);
    index = a + (anchor.time - times[a]) / Math.max(1, times[b] - times[a]);
  }
  return [left + (index + 0.5) * step, y(anchor.price)];
}
