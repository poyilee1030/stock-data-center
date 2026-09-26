import { describe, expect, it } from "vitest";
import { parse, stockHref } from "../src/lib/router";

describe("routes", () => {
  it("opens a stock on its price tab by default", () => {
    expect(parse("#/stock/2330")).toEqual({ page: "stock", stockId: "2330", tab: "price" });
  });

  it("opens the chip tab and the market page", () => {
    expect(parse("#/stock/2330/chips")).toEqual({ page: "stock", stockId: "2330", tab: "chips" });
    expect(parse("#/market")).toEqual({ page: "market" });
    expect(stockHref("6488", "chips")).toBe("#/stock/6488/chips");
    expect(stockHref("6488")).toBe("#/stock/6488");
    expect(parse("#/stock/2330/fundamentals")).toEqual({ page: "stock", stockId: "2330", tab: "fundamentals" });
    expect(stockHref("2330", "fundamentals")).toBe("#/stock/2330/fundamentals");
  });

  it("falls back to home for anything else", () => {
    expect(parse("#/stock/2330/nothing")).toEqual({ page: "home" });
    expect(parse("")).toEqual({ page: "home" });
  });
});
