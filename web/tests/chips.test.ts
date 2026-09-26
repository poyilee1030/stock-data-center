import { describe, expect, it } from "vitest";
import { chipPanels, concentrationPanel, distributionTable, type ChipAnswers } from "../src/lib/chips";
import { PALETTE } from "./palette";

const CALENDAR = ["2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17"];

function dated<T>(source: string, trade_date: string, values: T) {
  return { stock_id: "5236", source, trade_date, ...values };
}

// 5236 moved from TPEx to TWSE on 2026-07-16: each exchange's rows stand apart.
const ANSWERS: ChipAnswers = {
  flows: [dated("tpex_insti_daily_trade", "2026-07-14", { foreign_net: -100, trust_net: 0, dealer_net: 5, total_net: -95 }),
          dated("tpex_insti_daily_trade", "2026-07-15", { foreign_net: 200, trust_net: null, dealer_net: 1, total_net: 201 }),
          dated("twse_t86", "2026-07-16", { foreign_net: 9, trust_net: 9, dealer_net: 9, total_net: 27 })],
  streaks: [dated("tpex_insti_daily_trade", "2026-07-15", { foreign_streak_days: 1, trust_streak_days: 0, dealer_streak_days: 2 })],
  cumulative: [dated("tpex_insti_daily_trade", "2026-07-15", { trust_cumulative_net_ratio: 1.1116, dealer_cumulative_net_ratio: null })],
  foreign: [dated("mops_t13sa150_otc", "2026-07-14", { held_ratio: 12.5, investable_ratio: 87.5, foreign_legal_limit_ratio: 100 })],
  margin: [dated("tpex_margin_balance", "2026-07-15", { margin_balance: 28901000, short_balance: 0 })],
  marginMetrics: [dated("tpex_margin_balance", "2026-07-15", { margin_usage_ratio: 0.4458, short_usage_ratio: 0 })],
  lending: [dated("tpex_margin_sbl", "2026-07-15", { balance: 16244514, sold: 61000, returned: 8000 })],
};

function panel(id: string, exchange: "twse" | "tpex" = "tpex") {
  const built = chipPanels(ANSWERS, CALENDAR, exchange, PALETTE);
  const found = built.panels.find((p) => p.id === id);
  if (!found) throw new Error(`no panel ${id}`);
  return { built, found, series: (sid: string) => found.series.find((s) => s.id === sid)! };
}

describe("chipPanels", () => {
  it("spans one exchange's rows on the trading-day axis", () => {
    const { built } = panel("flows");
    expect(built.axis.dates).toEqual(["2026-07-14", "2026-07-15"]);
    expect(panel("flows", "twse").built.axis.dates).toEqual(["2026-07-16"]);
  });

  it("draws the three institutions' published net shares", () => {
    const { found, series } = panel("flows");
    expect(found.unit).toBe("shares");
    expect(found.dataset).toBe("institutional-flows");
    expect(series("foreign_net").values).toEqual([-100, 200]);
    expect(series("trust_net").values).toEqual([0, null]);
    expect(series("dealer_net").values).toEqual([5, 1]);
  });

  it("leaves a date a dataset has no row for empty", () => {
    expect(panel("streaks").series("foreign_streak_days").values).toEqual([null, 1]);
    expect(panel("foreign").series("held_ratio").values).toEqual([12.5, null]);
  });

  it("keeps margin and short balances in panels of their own", () => {
    expect(panel("margin").found.series.map((s) => s.id)).toEqual(["margin_balance"]);
    expect(panel("short").found.series.map((s) => s.id)).toEqual(["short_balance"]);
    expect(panel("short").series("short_balance").values).toEqual([null, 0]);
    expect(panel("usage").found.unit).toBe("percent");
    expect(panel("lending").series("balance").values).toEqual([null, 16244514]);
  });

  it("says the cumulative flow is a proxy, not a holding", () => {
    const { found, series } = panel("cumulative");
    expect(found.note).toMatch(/不是實際持股/);
    expect(series("trust_cumulative_net_ratio").values).toEqual([null, 1.1116]);
  });

  it("names each panel's source", () => {
    expect(panel("flows").found.sources).toEqual(["tpex_insti_daily_trade"]);
    expect(panel("flows", "twse").found.sources).toEqual(["twse_t86"]);
  });
});

describe("concentrationPanel", () => {
  it("uses the snapshot dates as its axis and the published ratios", () => {
    const rows = [
      { stock_id: "2330", source: "tdcc_opendata", snapshot_date: "2026-09-18", large_holder_ratio: 87.48, mid_holder_ratio: 3.62, small_holder_ratio: 8.83 },
      { stock_id: "2330", source: "tdcc_opendata", snapshot_date: "2026-09-11", large_holder_ratio: 87.57, mid_holder_ratio: null, small_holder_ratio: 8.76 },
    ];
    const built = concentrationPanel(rows, PALETTE);
    expect(built.axis).toEqual(["2026-09-11", "2026-09-18"]);
    expect(built.series.find((s) => s.id === "large_holder_ratio")!.values).toEqual([87.57, 87.48]);
    expect(built.series.find((s) => s.id === "mid_holder_ratio")!.values).toEqual([null, 3.62]);
  });
});

describe("distributionTable", () => {
  it("lists the fifteen levels, the signed adjustment and the total as published", () => {
    const row: Record<string, unknown> = { stock_id: "2330", source: "tdcc_opendata", snapshot_date: "2026-09-18",
      adjustment_shares: -1000, adjustment_percent: 0, total_holders: 3050518, total_shares: 25932370067, total_percent: 100 };
    for (let n = 1; n <= 15; n++) Object.assign(row, { [`holders_${n}`]: n, [`shares_${n}`]: n * 10, [`percent_${n}`]: n / 10 });
    row.holders_7 = null;
    const table = distributionTable(row as never);
    expect(table.levels).toHaveLength(15);
    expect(table.levels[0]).toEqual({ level: 1, range: "1-999", holders: 1, shares: 10, percent: 0.1 });
    expect(table.levels[14].range).toBe("1,000,001以上");
    expect(table.levels[6].holders).toBeNull();
    expect(table.adjustment).toEqual({ shares: -1000, percent: 0 });
    expect(table.total).toEqual({ holders: 3050518, shares: 25932370067, percent: 100 });
  });
});
