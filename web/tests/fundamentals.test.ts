import { describe, expect, it } from "vitest";
import {
  actionCells, monthAxis, reportRows, reportSeries, revenuePanels, valuationPanels, REPORT_ITEMS,
} from "../src/lib/fundamentals";
import { PALETTE } from "./palette";

function revenue(revenue_month: string, over: Record<string, unknown> = {}) {
  return { stock_id: "2330", source: "mops_t21sc03_sii", revenue_month, revenue: 100, mom_pct: 1.5, yoy_pct: 20,
           cumulative_yoy_pct: 10, note: "-", ...over };
}

describe("monthly revenue", () => {
  it("spans every calendar month from the first row to the last, a missing one left empty", () => {
    expect(monthAxis([revenue("2024-11-01"), revenue("2025-02-01")])).toEqual(
      ["2024-11-01", "2024-12-01", "2025-01-01", "2025-02-01"]);
    const built = revenuePanels([revenue("2024-11-01", { revenue: 7 }), revenue("2025-01-01", { yoy_pct: null })], PALETTE);
    expect(built.axis).toEqual(["2024-11-01", "2024-12-01", "2025-01-01"]);
    expect(built.revenue[0].values).toEqual([7, null, 100]);
    expect(built.growth.find((s) => s.id === "yoy_pct")!.values).toEqual([20, null, null]);
  });

  it("draws the published comparatives, not ones computed here", () => {
    const built = revenuePanels([revenue("2025-01-01", { mom_pct: -3.25, cumulative_yoy_pct: 39.26 })], PALETTE);
    expect(built.growth.map((s) => s.id)).toEqual(["yoy_pct", "mom_pct", "cumulative_yoy_pct"]);
    expect(built.growth.find((s) => s.id === "mom_pct")!.values).toEqual([-3.25]);
  });
});

function fact(account_code: string, period_start: string | null, period_end: string, value: number) {
  return { statement: "income_statement", account_code, concept: "c", period_start, period_end, unit: "u", value };
}

const Q2 = {
  stock_id: "2330", report_year: 2024, report_quarter: 2, report_category: "consolidated",
  available_at: "2024-08-15T15:59:59+00:00",
  facts: [
    fact("9750", "2024-01-01", "2024-06-30", 18.25), fact("9750", "2024-04-01", "2024-06-30", 9.56),
    // The prior year's comparatives, as this report restates them: never shown as 2023's own.
    fact("9750", "2023-01-01", "2023-06-30", 14.99), fact("9750", "2023-04-01", "2023-06-30", 7.01),
    fact("4000", "2024-01-01", "2024-06-30", 1266154378000),
  ],
};
const Q4 = {
  stock_id: "2330", report_year: 2024, report_quarter: 4, report_category: "consolidated",
  available_at: "2025-03-31T15:59:59+00:00",
  facts: [fact("9750", "2024-01-01", "2024-12-31", 45.25)],
};

describe("financial reports", () => {
  it("reads year to date and the single quarter from the report's own period", () => {
    const [q4, q2] = reportRows([Q2, Q4], "ytd");
    expect([q2.label, q2.values["9750"], q2.values["4000"]]).toEqual(["2024Q2", 18.25, 1266154378000]);
    expect(q4.values["9750"]).toBe(45.25);
    const single = reportRows([Q2, Q4], "quarter");
    expect(single.find((r) => r.label === "2024Q2")!.values["9750"]).toBe(9.56);
  });

  it("leaves the fourth quarter's single quarter empty: the annual report does not publish it", () => {
    const q4 = reportRows([Q4], "quarter")[0];
    expect(q4.values["9750"]).toBeNull();
    expect(q4.missingQuarter).toBe(true);
  });

  it("leaves an account the report does not carry empty", () => {
    expect(reportRows([Q4], "ytd")[0].values["8610"]).toBeNull();
  });

  it("refuses two different values for one account and period rather than pick one", () => {
    const twice = { ...Q4, facts: [fact("9750", "2024-01-01", "2024-12-31", 45.25), fact("9750", "2024-01-01", "2024-12-31", 45.3)] };
    expect(() => reportRows([twice], "ytd")).toThrow(/9750/);
  });

  it("puts quarters on their own axis, oldest first, for the EPS bars", () => {
    const built = reportSeries([Q4, Q2], "ytd", "9750", PALETTE);
    expect(built.axis).toEqual(["2024Q2", "2024Q4"]);
    expect(built.series[0].values).toEqual([18.25, 45.25]);
    expect(REPORT_ITEMS.map((i) => i.code)).toContain("9750");
  });
});

describe("valuation panels", () => {
  const CAL = ["2026-09-10", "2026-09-11"];
  const official = [{ stock_id: "2330", source: "twse_bwibbu_d", trade_date: "2026-09-11", pe_ratio: 27.94, pb_ratio: 9.72, dividend_yield: 0.91 }];
  const computed = [{ stock_id: "2330", source: "twse_mi_index", trade_date: "2026-09-10", ttm_eps: 86.28, pe_ratio: 27.5, pe_percentile: 73.98, roe: 34.78 }];

  it("keeps the published and the computed PE in panels of their own (CLAUDE.md §53)", () => {
    const built = valuationPanels(official, computed, CAL, "twse", PALETTE);
    const byId = Object.fromEntries(built.panels.map((p) => [p.id, p]));
    expect(byId["official-pe-pb"].dataset).toBe("official-valuations");
    expect(byId["official-pe-pb"].series.map((s) => s.id)).toEqual(["pe_ratio", "pb_ratio"]);
    expect(byId["computed-pe"].dataset).toBe("valuation-metrics");
    expect(byId["computed-pe"].series[0].values).toEqual([27.5, null]);
    expect(byId["official-pe-pb"].series[0].values).toEqual([null, 27.94]);
  });

  it("keeps only the chosen exchange's rows", () => {
    const tpex = valuationPanels(official, computed, CAL, "tpex", PALETTE);
    expect(tpex.axis.dates).toEqual([]);
  });
});

describe("corporate-action cells", () => {
  it("tells a term the source never publishes from one it left empty", () => {
    const row = { ex_date: "2026-09-16", source: "twse_twt49u", event_type: "息", cash_dividend_per_share: 7.000001,
                  free_share_ratio: null };
    const cells = actionCells(row);
    expect(cells.cash_dividend_per_share).toEqual({ kind: "value", value: 7.000001 });
    expect(cells.free_share_ratio).toEqual({ kind: "empty" });
    expect(cells.old_shares).toEqual({ kind: "unsourced" });
  });
});
