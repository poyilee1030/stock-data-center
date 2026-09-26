import { describe, expect, it } from "vitest";
import { latestIndices, marketFlowPanel, taiexPanel, indexPanel, TOTALS } from "../src/lib/market";
import { PALETTE } from "./palette";

const CALENDAR = ["2026-09-09", "2026-09-10", "2026-09-11"];

function index(source: string, index_name: string, trade_date: string, close_value: number | null, extra = {}) {
  return { source, index_name, trade_date, close_value, ...extra };
}

describe("latestIndices", () => {
  it("keeps each source's latest row per index, sources apart", () => {
    const rows = [
      index("twse_mi_index", "發行量加權股價指數", "2026-09-10", 22000, { change_points: 10, change_percent: 0.05 }),
      index("twse_mi_index", "發行量加權股價指數", "2026-09-11", 22100, { change_points: 100, change_percent: 0.45 }),
      index("twse_mi_5mins_hist", "發行量加權股價指數", "2026-09-11", 22100),
      index("tpex_index_summary", "指數:櫃買指數", "2026-09-11", 250),
    ];
    const latest = latestIndices(rows);
    expect(latest.map((r) => [r.source, r.index_name, r.trade_date])).toEqual([
      ["tpex_index_summary", "指數:櫃買指數", "2026-09-11"],
      ["twse_mi_5mins_hist", "發行量加權股價指數", "2026-09-11"],
      ["twse_mi_index", "發行量加權股價指數", "2026-09-11"],
    ]);
    expect(latest[2].change_points).toBe(100);
  });
});

describe("index panels", () => {
  it("draws TAIEX candles from the published open, high, low and close", () => {
    const rows = [index("twse_mi_5mins_hist", "發行量加權股價指數", "2026-09-10", 22000,
                        { open_value: 21900, high_value: 22050, low_value: 21800 })];
    const built = taiexPanel(rows, CALENDAR);
    expect(built.axis.dates).toEqual(["2026-09-10"]);
    expect(built.series[0].ohlc).toEqual([{ open: 21900, high: 22050, low: 21800, close: 22000 }]);
  });

  it("draws one index's close, leaving a day without a row empty", () => {
    const rows = [index("tpex_index_summary", "指數:櫃買指數", "2026-09-09", 250),
                  index("tpex_index_summary", "指數:櫃買指數", "2026-09-11", 252)];
    expect(indexPanel(rows, CALENDAR, PALETTE).series[0].values).toEqual([250, null, 252]);
  });

  it("refuses rows of two indices: they would be merged", () => {
    const rows = [index("tpex_index_summary", "指數:櫃買指數", "2026-09-09", 250),
                  index("tpex_index_summary", "指數:半導體業", "2026-09-10", 600)];
    expect(() => indexPanel(rows, CALENDAR, PALETTE)).toThrow();
  });
});

describe("marketFlowPanel", () => {
  const flow = (source: string, institution: string, trade_date: string, net: number | null) =>
    ({ source, trade_date, institution, buy: null, sell: null, net });

  it("draws each institution's published net, totals kept out of the bars", () => {
    const rows = [flow("twse_bfi82u", "投信", "2026-09-10", 5), flow("twse_bfi82u", "合計", "2026-09-10", 100),
                  flow("twse_bfi82u", "外資及陸資(不含外資自營商)", "2026-09-11", -7)];
    const built = marketFlowPanel(rows, CALENDAR, PALETTE);
    expect(built.series.map((s) => s.name)).toEqual(["外資及陸資(不含外資自營商)", "投信"]);
    expect(built.series[0].values).toEqual([null, -7]);
    expect(built.series[1].values).toEqual([5, null]);
    expect(TOTALS.has("合計")).toBe(true);
  });

  it("gives an institution the same colour on both exchanges", () => {
    const twse = marketFlowPanel([flow("twse_bfi82u", "投信", "2026-09-10", 1)], CALENDAR, PALETTE);
    const tpex = marketFlowPanel([flow("tpex_insti_summary", "投信", "2026-09-10", 1)], CALENDAR, PALETTE);
    expect(twse.series[0].color).toBe(tpex.series[0].color);
  });

  it("refuses an institution it does not know rather than guess its colour", () => {
    expect(() => marketFlowPanel([flow("twse_bfi82u", "新類別", "2026-09-10", 1)], CALENDAR, PALETTE)).toThrow(/新類別/);
  });
});
