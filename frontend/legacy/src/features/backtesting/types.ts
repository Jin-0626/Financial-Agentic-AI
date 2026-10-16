export type Row = { date: string; close: number };
export type Strategy = {
  id: string;
  name: string;
  description: string;
  parameters: Record<
    string,
    { title?: string; default?: number; minimum?: number; maximum?: number }
  >;
};
export type Catalog = { strategies: Strategy[]; execution: string };
export type Trade = {
  date: string;
  side: string;
  quantity: string;
  price: string;
  cost: string;
};
export type Result = {
  status: string;
  provenance: { currency: string; engine: string };
  result: {
    strategy: string;
    initial_cash: string;
    final_equity: string;
    total_return: number;
    equity: { date: string; equity: string }[];
    trades: Trade[];
    assumptions: string[];
    metrics: {
      maximum_drawdown: number;
      trade_count: number;
      total_costs: string;
      open_position: boolean;
    };
  };
};
export type Completed = {
  response: Result;
  inputs: Record<string, unknown>;
  source: string;
  label: string;
};
