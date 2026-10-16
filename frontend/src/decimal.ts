export function decimalParts(
  value: string,
): { value: bigint; scale: number } | null {
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(value) || value.length > 128)
    return null;
  const negative = value.startsWith("-"),
    [whole, fraction = ""] = value.replace(/^[+-]/, "").split(".");
  return {
    value: BigInt((whole || "0") + fraction) * (negative ? -1n : 1n),
    scale: fraction.length,
  };
}
export function decimalText(value: bigint, scale: number): string {
  const sign = value < 0n ? "-" : "",
    digits = (value < 0n ? -value : value).toString().padStart(scale + 1, "0");
  return (
    sign +
    (scale ? `${digits.slice(0, -scale)}.${digits.slice(-scale)}` : digits)
  );
}
export function normalizeDecimal(value: string): string | null {
  const parts = decimalParts(value);
  if (!parts) return null;
  let { value: n, scale } = parts;
  while (scale && n % 10n === 0n) {
    n /= 10n;
    scale--;
  }
  return decimalText(n, scale);
}
export function compareExact(a: string, b: string): number {
  const x = decimalParts(a),
    y = decimalParts(b);
  if (!x || !y) return 0;
  const scale = Math.max(x.scale, y.scale),
    left = x.value * 10n ** BigInt(scale - x.scale),
    right = y.value * 10n ** BigInt(scale - y.scale);
  return left < right ? -1 : left > right ? 1 : 0;
}
export function distance(a: string, b: string): string {
  const x = decimalParts(a),
    y = decimalParts(b);
  if (!x || !y) return "0";
  const scale = Math.max(x.scale, y.scale),
    value =
      x.value * 10n ** BigInt(scale - x.scale) -
      y.value * 10n ** BigInt(scale - y.scale);
  return decimalText(value < 0n ? -value : value, scale);
}
export function percent(value: string | null): string | null {
  const parts = value === null ? null : decimalParts(value);
  return parts ? decimalText(parts.value * 100n, parts.scale) : null;
}
export const minimum = (value: string | null, threshold: string | null) =>
  threshold === null ||
  (value !== null &&
    decimalParts(value) !== null &&
    compareExact(value, threshold) >= 0);
export const nonnegativeInteger = (value: string) =>
  value === "" ||
  (/^\d+$/.test(value) && BigInt(value) <= 9223372036854775807n);
