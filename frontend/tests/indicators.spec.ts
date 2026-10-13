import { test, expect } from "@playwright/test";
import {
  sma,
  ema,
  rsi,
  bollinger,
  vwap,
  macd,
  Candle,
} from "../src/features/terminal/indicators";
const candles = (values: number[]) =>
  values.map(
    (close, i) =>
      ({
        time: 1700000000 + i * 60,
        open: close,
        high: close,
        low: close,
        close,
        volume: 1,
        session: i < 3 ? "2026-10-01" : "2026-10-02",
      }) as Candle,
  );
test("moving averages preserve warmup and known values", () => {
  const c = candles([1, 2, 3, 4, 5]);
  expect(sma(c, 3).map((p) => p.value)).toEqual([2, 3, 4]);
  expect(ema(c, 3).map((p) => p.value)).toEqual([2, 3, 4]);
  expect(sma(c, 6)).toEqual([]);
  expect(sma(c, 3)[0].time).toBe(c[2].time);
});
test("RSI degeneracy and Bollinger population deviation", () => {
  expect(
    rsi(candles(Array(20).fill(10)), 14).every((p) => p.value === 50),
  ).toBe(true);
  expect(
    rsi(candles(Array.from({ length: 20 }, (_, i) => i + 1)), 14).every(
      (p) => p.value === 100,
    ),
  ).toBe(true);
  const b = bollinger(candles([1, 2, 3]), 3);
  expect(b.upper[0].value).toBeCloseTo(2 + 2 * Math.sqrt(2 / 3));
});
test("VWAP resets only in session mode and respects zero volume", () => {
  const c = candles([1, 2, 3, 10, 20]);
  expect(vwap(c, true).map((p) => p.value)).toEqual([1, 1.5, 2, 10, 15]);
  expect(vwap(c, false).at(-1)?.value).toBeCloseTo(7.2);
  expect(
    vwap(
      c.map((row) => ({ ...row, volume: 0 })),
      false,
    ),
  ).toEqual([]);
});
test("MACD signal is aligned and constant prices produce zero", () => {
  const c = candles(Array(50).fill(10));
  const result = macd(c);
  expect(result.line[0].time).toBe(c[25].time);
  expect(result.signal[0].time).toBe(c[33].time);
  expect(result.histogram.every((p) => p.value === 0)).toBe(true);
});
