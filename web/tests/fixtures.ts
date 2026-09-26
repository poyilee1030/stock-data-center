import type { AdjustedRow, IndicatorRow, PriceRow } from "../src/api/types";

export function price(trade_date: string, over: Partial<PriceRow> = {}): PriceRow {
  return {
    stock_id: "2330", source: "twse_mi_index", trade_date,
    open_price: 100, high_price: 105, low_price: 99, close_price: 104,
    volume: 1000, trade_value: 104000, trade_count: 10,
    price_change: 4, price_direction: "+",
    last_bid_price: 103.5, last_ask_price: 104, last_bid_volume: 1, last_ask_volume: 2,
    recorded_at: "2026-09-16T04:31:52+00:00", available_at: `${trade_date}T11:00:00+00:00`,
    provenance: { fetch_id: "f", raw_sha256: "s" },
    ...over,
  };
}

export function indicator(trade_date: string, over: Partial<IndicatorRow> = {}): IndicatorRow {
  return {
    stock_id: "2330", source: "twse_mi_index", trade_date,
    ma5: 101, ma10: 102, ma20: 103, ma60: 104, ma120: 105, ma240: 106,
    k: 70, d: 65, rsi6: 55, rsi12: 52, macd_dif: 1.5, macd_dea: 1.2, macd_hist: 0.3,
    bb_upper: 110, bb_middle: 103, bb_lower: 96,
    computed_at: "2026-09-24T14:46:25+00:00", available_at: `${trade_date}T11:00:00+00:00`,
    ...over,
  };
}

export function adjusted(trade_date: string, factor: number | null, over: Partial<AdjustedRow> = {}): AdjustedRow {
  const scale = (v: number) => (factor === null ? null : v * factor);
  return {
    stock_id: "2330", source: "twse_mi_index", trade_date,
    open_price: 100, high_price: 105, low_price: 99, close_price: 104,
    adjustment_factor: factor,
    adjusted_open_price: scale(100), adjusted_high_price: scale(105),
    adjusted_low_price: scale(99), adjusted_close_price: scale(104),
    ...over,
  };
}
