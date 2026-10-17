import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { createElement, useEffect, useId, useState } from "react";
import type { Payload, ResourceRequest } from "./resources";
import { normalizeDecimal } from "./decimal";
export type ResourceUpdate = {
  owner: string;
  generation: number;
  revision: number;
  request: ResourceRequest;
  payload: Payload | null;
  error: string | null;
  loading: boolean;
};
type Entry = {
  owner: string;
  key: string;
  callbacks: Set<(update: ResourceUpdate) => void>;
  latest: ResourceUpdate | null;
  epoch: number;
  generation: number | null;
  release?: ReturnType<typeof setTimeout>;
};
const entries = new Map<string, Entry>();
let listening: Promise<unknown> | null = null;
let handlersBound = false;
export function resourceKey(request: ResourceRequest): string {
  const decimal = (value: string | null | undefined) =>
    value == null ? null : (normalizeDecimal(value) ?? value);
  const normalized =
    request.type === "screener"
      ? {
          ...request,
          min_cap: decimal(request.min_cap),
          min_change: decimal(request.min_change),
          min_volume: decimal(request.min_volume),
        }
      : request;
  return JSON.stringify(normalized, Object.keys(normalized).sort());
}
function apply(update: ResourceUpdate) {
  const entry = entries.get(update.owner);
  if (
    !entry ||
    resourceKey(update.request) !== entry.key ||
    (entry.generation !== null && update.generation < entry.generation) ||
    (entry.latest &&
      (update.generation < entry.latest.generation ||
        (update.generation === entry.latest.generation &&
          update.revision < entry.latest.revision)))
  )
    return;
  entry.latest = update;
  for (const callback of entry.callbacks) callback(update);
}
async function begin(entry: Entry) {
  const epoch = ++entry.epoch;
  try {
    await ensureListening();
    if (
      entry.epoch !== epoch ||
      !entry.callbacks.size ||
      document.visibilityState === "hidden"
    )
      return;
    const update = await invoke<ResourceUpdate>("terminal_subscribe", {
      owner: entry.owner,
      request: JSON.parse(entry.key),
    });
    if (entry.epoch !== epoch || !entry.callbacks.size) {
      void invoke("terminal_unsubscribe", {
        owner: entry.owner,
        generation: update.generation,
      }).catch(() => {});
      return;
    }
    entry.generation = update.generation;
    apply(update);
  } catch (error) {
    if (entry.epoch === epoch) {
      const update: ResourceUpdate = {
        owner: entry.owner,
        request: JSON.parse(entry.key),
        generation: entry.generation ?? 0,
        revision: (entry.latest?.revision ?? 0) + 1,
        payload: entry.latest?.payload ?? null,
        error: String(error),
        loading: false,
      };
      entry.latest = update;
      for (const callback of entry.callbacks) callback(update);
    }
  }
}
function suspend(entry: Entry) {
  entry.epoch++;
  if (entry.generation !== null)
    void invoke("terminal_unsubscribe", {
      owner: entry.owner,
      generation: entry.generation,
    }).catch(() => {});
  entry.generation = null;
}
function ensureListening() {
  if (!listening) {
    listening = listen<ResourceUpdate[]>("terminal:resource", ({ payload }) => {
      if (Array.isArray(payload)) for (const update of payload) apply(update);
    }).catch((error) => {
      listening = null;
      throw error;
    });
  }
  if (!handlersBound) {
    handlersBound = true;
    document.addEventListener("visibilitychange", () => {
      for (const entry of entries.values()) {
        if (document.visibilityState === "hidden") suspend(entry);
        else if (entry.callbacks.size) void begin(entry);
      }
    });
    window.addEventListener("focus", () => {
      void invoke<Record<string, ResourceUpdate>>("terminal_resource_recover")
        .then((updates) => Object.values(updates).forEach(apply))
        .catch(() => {});
    });
  }
  return listening;
}
export function useSubscription(
  request: ResourceRequest | null,
  enabled = true,
  ownerOverride?: string,
) {
  const id = useId(),
    owner = ownerOverride ?? `resource:${id}`,
    key = request ? resourceKey(request) : null;
  const [state, setState] = useState<{
    key: string | null;
    update: ResourceUpdate | null;
  }>({ key: null, update: null });
  useEffect(() => {
    if (!key || !enabled) return;
    let entry = entries.get(owner);
    if (entry && entry.key !== key) {
      suspend(entry);
      if (entry.release) clearTimeout(entry.release);
      entries.delete(owner);
      entry = undefined;
    }
    const fresh = !entry;
    if (!entry) {
      entry = {
        owner,
        key,
        callbacks: new Set(),
        latest: null,
        epoch: 0,
        generation: null,
      };
      entries.set(owner, entry);
    }
    if (entry.release) clearTimeout(entry.release);
    const callback = (update: ResourceUpdate) => setState({ key, update });
    entry.callbacks.add(callback);
    if (entry.latest) callback(entry.latest);
    else setState({ key, update: null });
    if (fresh) void begin(entry);
    const current = entry;
    return () => {
      current.callbacks.delete(callback);
      if (!current.callbacks.size)
        current.release = setTimeout(() => {
          if (!current.callbacks.size) {
            suspend(current);
            if (entries.get(owner) === current) entries.delete(owner);
          }
        }, 0);
    };
  }, [owner, key, enabled]);
  const update = state.key === key ? state.update : null;
  return {
    payload: update?.payload ?? null,
    error: update?.error ?? null,
    loading: enabled && (update?.loading ?? !!key),
    refresh: () => {
      const entry = entries.get(owner);
      if (entry && entry.generation === null) {
        void begin(entry);
        return;
      }
      void invoke<Record<string, ResourceUpdate>>("terminal_resource_recover")
        .then((updates) => Object.values(updates).forEach(apply))
        .catch(() => {});
    },
  };
}

function QuoteOwner({
  instrument,
  owner,
}: {
  instrument: string;
  owner: string;
}) {
  useSubscription({ type: "quote", instrument }, true, owner);
  return null;
}
export function QuoteOwners({ ids, owner }: { ids: string[]; owner: string }) {
  return ids.map((instrument) =>
    createElement(QuoteOwner, {
      key: instrument,
      instrument,
      owner: `${owner}:${instrument}`,
    }),
  );
}
