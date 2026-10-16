import type { Chain, Contract } from "./resources";
import type { OptionsSettings } from "./browser-model";
import {
  compareExact,
  decimalParts,
  distance,
  minimum,
  normalizeDecimal,
} from "./decimal";
export function alignContracts(
  chain: Chain,
): { strike: string | null; calls: Contract[]; puts: Contract[] }[] {
  const groups = new Map<
    string,
    { strike: string | null; calls: Contract[]; puts: Contract[] }
  >();
  for (const side of ["calls", "puts"] as const)
    for (const contract of chain[side]) {
      const key =
          contract.strike === null ? null : normalizeDecimal(contract.strike),
        id = key ?? "unavailable";
      let row = groups.get(id);
      if (!row) {
        row = { strike: key, calls: [], puts: [] };
        groups.set(id, row);
      }
      row[side].push(contract);
    }
  return [...groups.values()].sort((a, b) =>
    a.strike === null
      ? b.strike === null
        ? 0
        : 1
      : b.strike === null
        ? -1
        : compareExact(a.strike, b.strike),
  );
}
export function filterChain(
  chain: Chain,
  settings: OptionsSettings,
  underlying: string | null,
) {
  const matches = (contract: Contract) =>
    (settings.moneyness === "all" ||
      (settings.moneyness === "itm" ? contract.itm : !contract.itm)) &&
    minimum(contract.volume, settings.min_volume) &&
    minimum(contract.open_interest, settings.min_open_interest) &&
    `${contract.contract} ${contract.strike ?? ""}`
      .toLocaleLowerCase()
      .includes(settings.search.trim().toLocaleLowerCase());
  const filtered = {
    ...chain,
    calls: settings.side === "puts" ? [] : chain.calls.filter(matches),
    puts: settings.side === "calls" ? [] : chain.puts.filter(matches),
  };
  const rows = alignContracts(filtered);
  if (
    settings.strike_window !== "nearest21" ||
    underlying === null ||
    !decimalParts(underlying)
  )
    return rows;
  const nearest = new Set(
    rows
      .filter(
        (row): row is typeof row & { strike: string } => row.strike !== null,
      )
      .sort(
        (a, b) =>
          compareExact(
            distance(a.strike, underlying),
            distance(b.strike, underlying),
          ) || compareExact(a.strike, b.strike),
      )
      .slice(0, 21)
      .map((row) => row.strike),
  );
  return rows.filter((row) => row.strike === null || nearest.has(row.strike));
}
export type SelectedContract = {
  side: "calls" | "puts";
  contract: Contract;
  occurrence: number;
};
