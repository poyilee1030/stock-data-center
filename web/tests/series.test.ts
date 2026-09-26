import { describe, expect, it } from "vitest";
import { align, bySource, column, defaultSource, tradingAxis } from "../src/lib/series";
import { price } from "./fixtures";

const CALENDAR = ["2024-07-01", "2024-07-02", "2024-07-03", "2024-07-04", "2024-07-05", "2024-07-08"];

describe("tradingAxis", () => {
  it("spans the trading days from the first price to the last", () => {
    const rows = [price("2024-07-02"), price("2024-07-05")];
    expect(tradingAxis(CALENDAR, rows).dates).toEqual(["2024-07-02", "2024-07-03", "2024-07-04", "2024-07-05"]);
  });

  it("keeps a suspended day as a slot of its own", () => {
    // 07-03 and 07-04 have no row: they stay on the axis, empty.
    const axis = tradingAxis(CALENDAR, [price("2024-07-02"), price("2024-07-05")]);
    expect(axis.dates).toContain("2024-07-03");
  });

  it("never drops a row the calendar does not know, and says so", () => {
    const rows = [price("2024-07-02"), price("2024-07-06"), price("2024-07-08")];
    const axis = tradingAxis(CALENDAR, rows);
    expect(axis.dates).toEqual(["2024-07-02", "2024-07-03", "2024-07-04", "2024-07-05", "2024-07-06", "2024-07-08"]);
    expect(axis.offCalendar).toEqual(["2024-07-06"]);
  });

  it("is empty without rows", () => {
    expect(tradingAxis(CALENDAR, [])).toEqual({ dates: [], offCalendar: [] });
  });
});

describe("align and column", () => {
  it("puts each row at its date and leaves the rest empty", () => {
    const axis = ["2024-07-02", "2024-07-03", "2024-07-04"];
    const aligned = align(axis, [price("2024-07-04", { close_price: 9 }), price("2024-07-02", { close_price: 7 })]);
    expect(aligned.map((r) => r?.close_price)).toEqual([7, undefined, 9]);
  });

  it("reads a missing row and a null value alike as missing, never zero", () => {
    const axis = ["2024-07-02", "2024-07-03", "2024-07-04"];
    const aligned = align(axis, [price("2024-07-02", { close_price: null }), price("2024-07-04", { close_price: 0 })]);
    expect(column(aligned, "close_price")).toEqual([null, null, 0]);
  });

  it("refuses two rows for one date: that would be two sources merged", () => {
    const rows = [price("2024-07-02"), price("2024-07-02", { source: "tpex_otc_quotes" })];
    expect(() => align(["2024-07-02"], rows)).toThrow(/2024-07-02/);
  });
});

describe("sources", () => {
  it("groups rows by source without merging them", () => {
    const rows = [price("2026-07-15", { source: "tpex_otc_quotes" }), price("2026-07-16", { source: "twse_mi_index" })];
    const groups = bySource(rows);
    expect([...groups.keys()].sort()).toEqual(["tpex_otc_quotes", "twse_mi_index"]);
    expect(groups.get("tpex_otc_quotes")!.map((r) => r.trade_date)).toEqual(["2026-07-15"]);
  });

  it("defaults to the source of the latest row", () => {
    // 5236 moved from TPEx to TWSE on 2026-07-16.
    const rows = [price("2026-07-16", { source: "twse_mi_index" }), price("2026-07-15", { source: "tpex_otc_quotes" })];
    expect(defaultSource(rows)).toBe("twse_mi_index");
    expect(defaultSource([])).toBeNull();
  });
});
