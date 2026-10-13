import { useCallback, useEffect, useRef, useState } from "react";
import { api, Identity, scope } from "../../shared/api";
import { Document, Summary } from "./types";
export function usePortfolioWorkspace(identity: Identity) {
  const savedKey = `portfolio:${identity.org}:${identity.user}`;
  const [list, setList] = useState<
    { id: string; name: string; currency: string }[]
  >([]);
  const [id, setId] = useState("");
  const [doc, setDoc] = useState<Document | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const refreshing = useRef<string | null>(null);
  const reloadList = useCallback(
    async (select?: string) => {
      const data = await api<{ portfolios: typeof list }>(
        `/portfolio/list?${scope(identity)}`,
      );
      setList(data.portfolios);
      const choice = select || localStorage.getItem(savedKey);
      setId(
        data.portfolios.find((p) => p.id === choice)?.id ||
          data.portfolios[0]?.id ||
          "",
      );
    },
    [identity, savedKey],
  );
  useEffect(() => {
    void reloadList().catch((e) => setError(e.message));
  }, [reloadList]);
  const refresh = useCallback(async () => {
    if (!id || refreshing.current === id) return;
    refreshing.current = id;
    setBusy(true);
    setError("");
    const ticket = generation.current;
    try {
      const document = await api<Document>(`/portfolio?${scope(identity, id)}`);
      if (ticket !== generation.current) return;
      setDoc(document);
      const valuation = await api<Summary>(
        `/portfolio/summary?${scope(identity, id)}`,
      );
      if (ticket !== generation.current) return;
      setSummary(valuation);
      setDoc({ ...document, revision: valuation.revision });
    } catch (e) {
      if (ticket === generation.current) setError((e as Error).message);
    } finally {
      if (refreshing.current === id) {
        refreshing.current = null;
        setBusy(false);
      }
    }
  }, [id, identity]);
  useEffect(() => {
    generation.current++;
    setDoc(null);
    setSummary(null);
    if (id) localStorage.setItem(savedKey, id);
    void refresh();
    return () => {
      generation.current++;
      refreshing.current = null;
    };
  }, [id, savedKey, refresh]);
  useEffect(() => {
    const tick = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    const timer = setInterval(tick, 300000);
    document.addEventListener("visibilitychange", tick);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [refresh]);
  async function act(work: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return {
    list,
    id,
    setId,
    doc,
    setDoc,
    summary,
    error,
    setError,
    busy,
    reloadList,
    refresh,
    act,
  };
}
