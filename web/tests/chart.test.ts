import { describe, expect, it } from "vitest";
import type { AdjustmentEvent } from "../src/api/types";
import { buildChartOption, type ChartInput, type Palette } from "../src/lib/chart";
import { align } from "../src/lib/series";
import { adjusted, indicator, price } from "./fixtures";

const PALETTE: Palette = {
  up: "#e5484d", down: "#30a46c", flat: "#8b8d98", text: "#eee", muted: "#999", grid: "#333",
  surface: "#111", accent: "#f5a524",
  ma: { 5: "#a", 10: "#b", 20: "#c", 60: "#d", 120: "#e", 240: "#f" },
  bollinger: "#bb", k: "#k", d: "#dd", rsi6: "#r6", rsi12: "#r12", dif: "#dif", dea: "#dea",
};

const AXIS = ["2024-06-11", "2024-06-12", "2024-06-13", "2024-06-14"];
const PRICES = [
  price("2024-06-11", { open_price: 892, high_price: 895, low_price: 883, close_price: 883, volume: 5, price_change: -26, price_direction: "-" }),
  price("2024-06-12", { open_price: 888, high_price: 914, low_price: 888, close_price: 909, volume: 7, price_change: 26, price_direction: "+" }),
  // 06-13 has no row: a suspended day.
  price("2024-06-14", { open_price: null, high_price: null, low_price: null, close_price: null, volume: 0, price_change: 0, price_direction: " " }),
];
const INDICATORS = [indicator("2024-06-11", { ma5: 880.5, ma240: null }), indicator("2024-06-12", { ma5: 890.25 }), indicator("2024-06-14")];
const ADJUSTED = [adjusted("2024-06-11", 0.96), adjusted("2024-06-12", 0.96), adjusted("2024-06-14", null)];
const EVENT: AdjustmentEvent = {
  stock_id: "2330", source: "twse_twt49u", ex_date: "2024-06-13", event_type: "息",
  close_before: 909, reference_price: 905.5, factor: 0.99614, available_at: "2024-06-12T16:00:00+00:00",
};

function input(over: Partial<ChartInput> = {}): ChartInput {
  return {
    axis: AXIS, prices: align(AXIS, PRICES), indicators: align(AXIS, INDICATORS),
    adjusted: align(AXIS, ADJUSTED), events: [EVENT], mode: "raw",
    ma: [5, 20, 240], bollinger: false, indicator: "kd", palette: PALETTE, ...over,
  };
}

function series(option: ReturnType<typeof buildChartOption>, id: string): any {
  const found = (option.series as any[]).find((s) => s.id === id);
  if (!found) throw new Error(`no series ${id}`);
  return found;
}

function ids(option: ReturnType<typeof buildChartOption>): string[] {
  return (option.series as any[]).map((s) => s.id);
}

describe("buildChartOption, raw prices", () => {
  it("uses the trading-day axis on every grid", () => {
    const option = buildChartOption(input());
    for (const axis of option.xAxis as any[]) expect(axis.data).toEqual(AXIS);
  });

  it("draws each candle from the API's own open, close, low and high", () => {
    const candles = series(buildChartOption(input()), "candles");
    expect(candles.data[0]).toEqual([892, 883, 883, 895]);
    expect(candles.data[1]).toEqual([888, 909, 888, 914]);
  });

  it("leaves a day without a row, or without a price, empty", () => {
    const candles = series(buildChartOption(input()), "candles");
    expect(candles.data[2]).toBe("-");
    expect(candles.data[3]).toBe("-");
  });

  it("colours a TPEx volume bar by its signed change: TPEx has no direction column", () => {
    const tpex = [price("2024-06-11", { source: "tpex_otc_quotes", price_change: -16, price_direction: null }),
                  price("2024-06-12", { source: "tpex_otc_quotes", price_change: 3, price_direction: "X" })];
    const volume = series(buildChartOption(input({ prices: align(AXIS, tpex) })), "volume");
    expect(volume.data[0].itemStyle.color).toBe(PALETTE.down);
    expect(volume.data[1].itemStyle.color).toBe(PALETTE.flat);
  });

  it("draws the volume the API gave, zero included, coloured by the published change", () => {
    const volume = series(buildChartOption(input()), "volume");
    expect(volume.data.map((d: any) => (d === "-" ? d : d.value))).toEqual([5, 7, "-", 0]);
    expect(volume.data[0].itemStyle.color).toBe(PALETTE.down);
    expect(volume.data[1].itemStyle.color).toBe(PALETTE.up);
    expect(volume.data[3].itemStyle.color).toBe(PALETTE.flat);
  });

  it("draws only the chosen moving averages, each point the API's value or a gap", () => {
    const option = buildChartOption(input());
    expect(ids(option).filter((id) => id.startsWith("ma"))).toEqual(["ma5", "ma20", "ma240"]);
    expect(series(option, "ma5").data).toEqual([880.5, 890.25, null, 101]);
    expect(series(option, "ma240").data).toEqual([null, 106, null, 106]);
    expect(series(option, "ma5").connectNulls).toBe(false);
  });

  it("adds the Bollinger bands when asked", () => {
    expect(ids(buildChartOption(input()))).not.toContain("bb_upper");
    const option = buildChartOption(input({ bollinger: true }));
    expect(series(option, "bb_upper").data).toEqual([110, 110, null, 110]);
    expect(series(option, "bb_lower").data).toEqual([96, 96, null, 96]);
  });

  it("marks no corporate action on raw prices", () => {
    expect(series(buildChartOption(input()), "candles").markLine).toBeUndefined();
  });
});

describe("buildChartOption, adjusted prices", () => {
  it("draws the API's adjusted prices, not raw ones scaled here", () => {
    const candles = series(buildChartOption(input({ mode: "adjusted" })), "candles");
    const row = ADJUSTED[0];
    expect(candles.data[0]).toEqual([row.adjusted_open_price, row.adjusted_close_price,
                                     row.adjusted_low_price, row.adjusted_high_price]);
  });

  it("leaves a date whose factor is unknown empty", () => {
    expect(series(buildChartOption(input({ mode: "adjusted" })), "candles").data[3]).toBe("-");
  });

  it("hides the moving averages and bands, which are computed on raw close", () => {
    const option = buildChartOption(input({ mode: "adjusted", bollinger: true }));
    expect(ids(option).some((id) => id.startsWith("ma") || id.startsWith("bb_"))).toBe(false);
  });

  it("marks each applied corporate action on its ex-date", () => {
    // A vertical line, so an ex-date without a trade is still marked.
    const mark = series(buildChartOption(input({ mode: "adjusted" })), "candles").markLine;
    expect(mark.data).toHaveLength(1);
    expect(mark.data[0].xAxis).toBe("2024-06-13");
    expect(mark.data[0].label.formatter).toBe("息");
  });

  it("marks no event whose ex-date is off the axis", () => {
    const later = { ...EVENT, ex_date: "2025-06-12" };
    const mark = series(buildChartOption(input({ mode: "adjusted", events: [later] })), "candles").markLine;
    expect(mark.data).toHaveLength(0);
  });
});

describe("buildChartOption, indicator panel", () => {
  it("draws KD from the API", () => {
    const option = buildChartOption(input({ indicator: "kd" }));
    expect(series(option, "k").data).toEqual([70, 70, null, 70]);
    expect(series(option, "d").data).toEqual([65, 65, null, 65]);
  });

  it("draws RSI and MACD from the API", () => {
    const rsi = buildChartOption(input({ indicator: "rsi" }));
    expect(series(rsi, "rsi6").data).toEqual([55, 55, null, 55]);
    expect(series(rsi, "rsi12").data).toEqual([52, 52, null, 52]);
    const macd = buildChartOption(input({ indicator: "macd" }));
    expect(series(macd, "macd_dif").data).toEqual([1.5, 1.5, null, 1.5]);
    expect(series(macd, "macd_dea").data).toEqual([1.2, 1.2, null, 1.2]);
    expect(series(macd, "macd_hist").data.map((d: any) => (d === "-" ? d : d.value))).toEqual([0.3, 0.3, "-", 0.3]);
  });

  it("keeps the indicators in adjusted mode: the panel says they are on raw close", () => {
    expect(ids(buildChartOption(input({ mode: "adjusted", indicator: "kd" })))).toContain("k");
  });
});
