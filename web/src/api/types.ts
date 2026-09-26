// The shapes of the public API's answers (docs/api.md), only the fields the
// dashboard reads. Numbers arrive as JSON numbers; a null is a value the
// source did not give, never zero.

export interface Pit {
  mode: "market" | "system";
  information_as_of?: string;
  knowledge_as_of?: string;
  system_as_of?: string;
  defaulted?: string[];
}

export interface Provenance {
  fetch_id: string;
  raw_sha256: string | null;
}

export interface Listing {
  market: "sii" | "otc";
  listed_on: string | null;
  delisted_on: string | null;
}

export interface Stock {
  stock_id: string;
  name: string;
  industry: string | null;
  market: "sii" | "otc" | null;
  listed_on: string | null;
  listings: Listing[];
}

export interface StocksAnswer {
  universe: string;
  rows: Stock[];
}

export interface TradingDaysAnswer {
  rows: { trade_date: string }[];
}

export interface Dated {
  stock_id: string;
  source: string;
  trade_date: string;
}

export interface PriceRow extends Dated {
  open_price: number | null;
  high_price: number | null;
  low_price: number | null;
  close_price: number | null;
  volume: number | null;
  trade_value: number | null;
  trade_count: number | null;
  price_change: number | null;
  price_direction: string | null;
  last_bid_price: number | null;
  last_ask_price: number | null;
  last_bid_volume: number | null;
  last_ask_volume: number | null;
  recorded_at: string;
  available_at: string;
  provenance: Provenance;
}

export const MA_WINDOWS = [5, 10, 20, 60, 120, 240] as const;
export type MaWindow = (typeof MA_WINDOWS)[number];

export interface IndicatorRow extends Dated {
  ma5: number | null;
  ma10: number | null;
  ma20: number | null;
  ma60: number | null;
  ma120: number | null;
  ma240: number | null;
  k: number | null;
  d: number | null;
  rsi6: number | null;
  rsi12: number | null;
  macd_dif: number | null;
  macd_dea: number | null;
  macd_hist: number | null;
  bb_upper: number | null;
  bb_middle: number | null;
  bb_lower: number | null;
  computed_at: string;
  available_at: string;
}

export interface AdjustedRow extends Dated {
  open_price: number | null;
  high_price: number | null;
  low_price: number | null;
  close_price: number | null;
  adjustment_factor: number | null;
  adjusted_open_price: number | null;
  adjusted_high_price: number | null;
  adjusted_low_price: number | null;
  adjusted_close_price: number | null;
}

export interface AdjustmentEvent {
  stock_id: string;
  source: string;
  ex_date: string;
  event_type: string;
  close_before: number | null;
  reference_price: number | null;
  factor: number | null;
  available_at: string;
}

export interface Derivation {
  dataset_code: string;
  derivation_version: string;
  price_adjustment_convention?: string;
  git_commit?: string;
}

export interface RowsAnswer<Row> {
  dataset: string;
  pit: Pit;
  derivation?: Derivation;
  unsourced?: Record<string, string[]>;
  rows: Row[];
}

export interface AdjustedAnswer extends RowsAnswer<AdjustedRow> {
  events: AdjustmentEvent[];
}
