import { describe, expect, it } from "vitest";
import { compact, grouped, priceText, taipei } from "../src/lib/format";

describe("format", () => {
  it("shows an instant in Taipei time", () => {
    expect(taipei("2026-09-26T03:21:21.735821+00:00")).toBe("2026-09-26 11:21");
    expect(taipei("2024-07-01T19:00:00+00:00")).toBe("2024-07-02 03:00");
  });

  it("abbreviates large numbers in 萬 and 億", () => {
    expect(compact(20320957284)).toBe("203.21 億");
    expect(compact(20936005)).toBe("2,093.6 萬");
    expect(compact(9999)).toBe("9,999");
    expect(compact(-150000000)).toBe("-1.50 億");
  });

  it("writes a missing value as a dash, never as zero", () => {
    expect(compact(null)).toBe("—");
    expect(grouped(null)).toBe("—");
    expect(priceText(null)).toBe("—");
    expect(priceText(0)).toBe("0.00");
  });

  it("groups thousands and shows prices to two places", () => {
    expect(grouped(38293)).toBe("38,293");
    expect(priceText(968)).toBe("968.00");
    expect(priceText(853.2065719374704)).toBe("853.21");
    expect(priceText(1760)).toBe("1,760.00");
  });
});
