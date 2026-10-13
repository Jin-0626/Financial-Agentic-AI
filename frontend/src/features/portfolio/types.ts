export type Holding = {
  symbol: string;
  quantity: string;
  average_cost: string;
  market_value?: string;
  cost_basis?: string;
  unrealized_gain?: string;
  weight_percent?: string;
  price?: string;
  name?: string;
  sector?: string;
  market_state?: string;
  as_of?: number;
  change_percent?: number;
};
export type Transaction = {
  id: string;
  symbol: string;
  side: string;
  quantity: string;
  price: string;
  date: string;
  realized_gain?: string;
  notes?: string;
};
export type Document = {
  id: string;
  revision: number;
  portfolio: {
    name: string;
    owner?: string;
    currency: string;
    benchmark: string;
    period: string;
    positions: Holding[];
  };
  transactions: Transaction[];
  transaction_corrections?: {
    previous: Transaction;
    replacement: Transaction;
    corrected_at: string;
  }[];
};
export type Performance = {
  dates: string[];
  portfolio: number[];
  benchmark: number[];
  method: string;
  limitations: string;
  portfolio_return_percent: number;
  benchmark_return_percent: number;
};
export type Summary = {
  sectors?: { name: string; market_value: string; weight_percent: string }[];
  revision: number;
  positions: Holding[];
  market_value: string | null;
  cost_basis: string | null;
  unrealized_gain: string | null;
  realized_gain?: string;
  dividends?: string;
  currency: string;
  fetched_at: string;
  comparison?: Performance;
  projection?: Performance;
  comparison_error?: string;
  errors: { symbol?: string; error: string }[];
  history: { date: string; market_value: string; cost_basis: string }[];
  performance_reset_date?: string;
};
export type Preview = {
  preview_hash: string;
  expected_revision: number;
  transaction_count: number;
  document: Document;
};
