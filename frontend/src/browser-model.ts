export type Universe = "MY" | "US" | "crypto";
export type MarketColumn =
  | "instrument"
  | "name"
  | "price"
  | "change_percent"
  | "volume"
  | "market_cap"
  | "sector"
  | "currency"
  | "exchange"
  | "status";
export type MarketSort =
  "market_cap" | "change_percent" | "volume" | "symbol" | "price";
export type MarketSettings = {
  columns: MarketColumn[];
  sort: MarketSort;
  descending: boolean;
  sector: string | null;
  min_cap: string | null;
  min_change: string | null;
  min_volume: string | null;
  filter: string;
};
export type OptionColumn =
  "bid" | "ask" | "last" | "implied_volatility" | "volume" | "open_interest";
export type OptionsSettings = {
  columns: OptionColumn[];
  side: "calls" | "both" | "puts";
  moneyness: "all" | "itm" | "otm";
  min_volume: string | null;
  min_open_interest: string | null;
  search: string;
  strike_window: "all" | "nearest21";
};
export type Preset<T> = { name: string; settings: T };
export type BrowserPreferences = {
  my: MarketSettings;
  us: MarketSettings;
  crypto: MarketSettings;
  options: OptionsSettings;
  market_presets: Preset<MarketSettings>[];
  options_presets: Preset<OptionsSettings>[];
};
export type PresetAction =
  | { type: "save"; name: string; duplicate_from: string | null }
  | { type: "rename"; name: string; new_name: string }
  | { type: "delete" | "apply"; name: string }
  | { type: "reset" };
export type BrowserMutation =
  | { type: "market"; market: Universe; settings: MarketSettings }
  | { type: "options"; settings: OptionsSettings }
  | { type: "market_preset"; market: Universe; action: PresetAction }
  | { type: "options_preset"; action: PresetAction };
export const defaultMarket = (): MarketSettings => ({
  columns: [
    "instrument",
    "price",
    "change_percent",
    "market_cap",
    "volume",
    "sector",
  ],
  sort: "market_cap",
  descending: true,
  sector: null,
  min_cap: null,
  min_change: null,
  min_volume: null,
  filter: "",
});
export const defaultOptions = (): OptionsSettings => ({
  columns: [
    "bid",
    "ask",
    "last",
    "implied_volatility",
    "open_interest",
    "volume",
  ],
  side: "both",
  moneyness: "all",
  min_volume: null,
  min_open_interest: null,
  search: "",
  strike_window: "all",
});
export const defaultBrowser = (): BrowserPreferences => ({
  my: defaultMarket(),
  us: defaultMarket(),
  crypto: defaultMarket(),
  options: defaultOptions(),
  market_presets: [],
  options_presets: [],
});
export const marketKey = (market: Universe) =>
  market === "MY" ? "my" : market === "US" ? "us" : "crypto";
export const marketLabels: Record<MarketColumn, string> = {
  instrument: "Instrument",
  name: "Name",
  price: "Last",
  change_percent: "Change %",
  volume: "Volume",
  market_cap: "Market cap",
  sector: "Sector",
  currency: "Currency",
  exchange: "Exchange",
  status: "Data status",
};
export const optionLabels: Record<OptionColumn, string> = {
  bid: "Bid",
  ask: "Ask",
  last: "Last",
  implied_volatility: "IV %",
  volume: "Volume",
  open_interest: "OI",
};
