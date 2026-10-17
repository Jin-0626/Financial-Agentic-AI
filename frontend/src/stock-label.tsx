import { createContext, useContext, useMemo, type ReactNode } from "react";
import { symbol, type Snapshot } from "./bridge";

const Names = createContext<ReadonlyMap<string, string>>(new Map());

export function StockNames({
  snapshot,
  children,
}: {
  snapshot: Snapshot | null;
  children: ReactNode;
}) {
  const names = useMemo(() => {
    const result = new Map<string, string>();
    for (const [id, market] of Object.entries(snapshot?.quotes ?? {})) {
      const name = market.data?.instrument.name;
      if (name && name !== symbol(id)) result.set(id, name);
    }
    return result;
  }, [snapshot?.quotes]);
  return <Names.Provider value={names}>{children}</Names.Provider>;
}

export function StockLabel({
  code,
  name,
}: {
  code: string;
  name?: string | null;
}) {
  const names = useContext(Names);
  const display = symbol(code);
  const supplied = name?.trim();
  const resolved =
    (supplied && supplied !== display ? supplied : null) ??
    names.get(code.includes(":") ? code : `yahoo:${code}`) ??
    "Name unavailable";
  return (
    <span className="stock-label" title={`${display} · ${resolved}`}>
      <span className="stock-code">{display}</span>{" "}
      <span className="stock-name">{resolved}</span>
    </span>
  );
}
