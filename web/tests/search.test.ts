import { describe, expect, it } from "vitest";
import type { Stock } from "../src/api/types";
import { searchStocks } from "../src/lib/search";

function stock(stock_id: string, name: string, market: Stock["market"] = "sii"): Stock {
  return { stock_id, name, industry: null, market, listed_on: null, listings: [] };
}

const STOCKS = [stock("2330", "台積電"), stock("2303", "聯電"), stock("6488", "環球晶", "otc"),
                stock("1234", "黑松"), stock("3330", "某公司"), stock("2331", "精英", null)];

describe("searchStocks", () => {
  it("puts the exact code first, then code prefixes, then names", () => {
    expect(searchStocks(STOCKS, "2330").map((s) => s.stock_id)).toEqual(["2330"]);
    expect(searchStocks(STOCKS, "233").map((s) => s.stock_id)).toEqual(["2330", "2331"]);
    expect(searchStocks(STOCKS, "23").map((s) => s.stock_id)).toEqual(["2303", "2330", "2331"]);
  });

  it("matches a name anywhere in it", () => {
    expect(searchStocks(STOCKS, "積").map((s) => s.stock_id)).toEqual(["2330"]);
    expect(searchStocks(STOCKS, "晶").map((s) => s.stock_id)).toEqual(["6488"]);
  });

  it("ranks listed companies before delisted ones", () => {
    const hits = searchStocks([stock("9999", "甲", null), stock("9998", "甲乙")], "甲");
    expect(hits.map((s) => s.stock_id)).toEqual(["9998", "9999"]);
  });

  it("returns nothing for a blank query and caps the list", () => {
    expect(searchStocks(STOCKS, "  ")).toEqual([]);
    expect(searchStocks(STOCKS, "2", 2)).toHaveLength(2);
  });
});
