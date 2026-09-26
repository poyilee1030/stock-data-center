import { describe, expect, it } from "vitest";
import { exchangeOf, rowsOfExchange } from "../src/lib/exchange";

// Each exchange's feeds are a stock's history on that market; a stock that
// moved market has two, never merged (CLAUDE.md §30).
describe("exchangeOf", () => {
  it("names the exchange of every daily source the stock page reads", () => {
    for (const s of ["twse_mi_index", "twse_t86", "twse_mi_qfiis", "twse_mi_margn", "twse_twt93u", "twse_bwibbu_d"]) {
      expect(exchangeOf(s), s).toBe("twse");
    }
    for (const s of ["tpex_otc_quotes", "tpex_insti_daily_trade", "mops_t13sa150_otc",
                     "tpex_margin_balance", "tpex_margin_sbl", "tpex_pe_qry_date"]) {
      expect(exchangeOf(s), s).toBe("tpex");
    }
  });

  it("gives MOPS monthly revenue no exchange: MOPS files it by today's market", () => {
    // Code review of #71: 4736 moved from TPEx to TWSE on 2023-12-22, and all
    // 80 of its months, OTC ones included, are under mops_t21sc03_sii.
    expect(() => exchangeOf("mops_t21sc03_sii")).toThrow();
    expect(() => exchangeOf("mops_t21sc03_otc")).toThrow();
  });

  it("refuses a source it does not know rather than guess", () => {
    expect(() => exchangeOf("tpex_insti_qfii")).toThrow(/tpex_insti_qfii/);
  });

  it("keeps only one exchange's rows", () => {
    const rows = [{ source: "twse_t86", trade_date: "2026-07-16" }, { source: "tpex_insti_daily_trade", trade_date: "2026-07-15" }];
    expect(rowsOfExchange(rows, "tpex").map((r) => r.trade_date)).toEqual(["2026-07-15"]);
  });
});
