import { useEffect, useState } from "react";
import { api } from "../../shared/api";
export type Market<T> = {
  status: "ok" | "stale" | "unavailable";
  data: T | null;
  source?: string | null;
  fetched_at?: string;
  observed_at?: string | null;
  coverage?: string;
  stale?: boolean;
  error?: string;
  retry_after?: number;
  limitations?: string[];
  currency?: string;
  [key: string]: unknown;
};
type Entry = {
  value: Market<unknown> | null;
  listeners: Set<() => void>;
  timer?: ReturnType<typeof setTimeout>;
  controller?: AbortController;
  running: boolean;
  interval: number;
  failures: number;
  updatedAt: number;
};
const entries = new Map<string, Entry>();
function emit(entry: Entry) {
  entry.listeners.forEach((fn) => fn());
}
function schedule(path: string, entry: Entry, delay = entry.interval) {
  clearTimeout(entry.timer);
  if (entry.listeners.size && !document.hidden)
    entry.timer = setTimeout(() => void load(path, entry), delay);
}
async function load(path: string, entry: Entry) {
  if (entry.running || document.hidden || !entry.listeners.size) return;
  entry.running = true;
  entry.controller = new AbortController();
  try {
    const value = await api<Market<unknown>>(path, {
      signal: entry.controller.signal,
    });
    if (!value || !["ok", "stale", "unavailable"].includes(value.status))
      throw new Error("Invalid market response");
    entry.value = value;
    entry.updatedAt = Date.now();
    entry.failures = value.status === "ok" ? 0 : entry.failures + 1;
  } catch (error) {
    if (!entry.controller.signal.aborted) {
      entry.failures++;
      entry.value = {
        ...(entry.value || { data: null }),
        status: entry.value?.data ? "stale" : "unavailable",
        error: (error as Error).message,
      };
    }
  } finally {
    entry.running = false;
    emit(entry);
    schedule(
      path,
      entry,
      Math.max(
        (entry.value?.retry_after || 0) * 1000,
        Math.min(300000, entry.interval * 2 ** Math.min(entry.failures, 4)),
      ),
    );
  }
}
export function useMarket<T>(path: string, interval: number, active = true) {
  const [value, setValue] = useState<{ path: string; value: Market<T> | null }>(
    { path, value: null },
  );
  useEffect(() => {
    if (!active) return;
    let entry = entries.get(path);
    if (!entry) {
      entry = {
        value: null,
        listeners: new Set(),
        running: false,
        interval,
        failures: 0,
        updatedAt: 0,
      };
      entries.set(path, entry);
    }
    const current = entry;
    current.interval = Math.min(current.interval, interval);
    const notify = () =>
      setValue({ path, value: current.value as Market<T> | null });
    current.listeners.add(notify);
    notify();
    if (
      !current.running &&
      (!current.value || Date.now() - current.updatedAt >= interval)
    )
      void load(path, current);
    else schedule(path, current);
    const visibility = () => {
      clearTimeout(current.timer);
      if (!document.hidden) void load(path, current);
    };
    document.addEventListener("visibilitychange", visibility);
    return () => {
      current.listeners.delete(notify);
      document.removeEventListener("visibilitychange", visibility);
      if (!current.listeners.size) {
        clearTimeout(current.timer);
        current.controller?.abort();
        entries.delete(path);
      }
    };
  }, [path, interval, active]);
  return value.path === path ? value.value : null;
}
export function display(value: unknown, digits = 2) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  return Number.isFinite(n)
    ? n.toLocaleString(undefined, { maximumFractionDigits: digits })
    : "—";
}
