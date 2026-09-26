import { describe, expect, it } from "vitest";
import { exchangeOf, rowsOfExchange } from "../src/lib/exchange";

// Each exchange's feeds are a stock's history on that market; a stock that
// moved market has two, never merged (CLAUDE.md §30).
describe("exchangeOf", () => {
  it("names the exchange of every daily source the stock page reads", () => {
    for (const s of ["twse_mi_index", "twse_t86", "twse_mi_qfiis", "twse_mi_margn", "twse_twt93u"]) {
      expect(exchangeOf(s), s).toBe("twse");
    }
    for (const s of ["tpex_otc_quotes", "tpex_insti_daily_trade", "mops_t13sa150_otc",
                     "tpex_margin_balance", "tpex_margin_sbl"]) {
      expect(exchangeOf(s), s).toBe("tpex");
    }
  });

  it("refuses a source it does not know rather than guess", () => {
    expect(() => exchangeOf("tpex_insti_qfii")).toThrow(/tpex_insti_qfii/);
  });

  it("keeps only one exchange's rows", () => {
    const rows = [{ source: "twse_t86", trade_date: "2026-07-16" }, { source: "tpex_insti_daily_trade", trade_date: "2026-07-15" }];
    expect(rowsOfExchange(rows, "tpex").map((r) => r.trade_date)).toEqual(["2026-07-15"]);
  });
});
