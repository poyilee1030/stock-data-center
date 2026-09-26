import { describe, expect, it } from "vitest";
import { changeText, movement } from "../src/lib/movement";

// Both feeds publish a signed price_change; TWSE adds a +/-/X column, TPEx has
// none and prints only its 不比價 marker, stored as X (audit, daily_prices).
describe("movement", () => {
  it("reads the published signed change", () => {
    expect(movement({ price_change: -8, price_direction: "-" })).toBe("down");
    expect(movement({ price_change: 2, price_direction: "+" })).toBe("up");
    expect(movement({ price_change: 0, price_direction: " " })).toBe("flat");
  });

  it("works for TPEx rows, which have no direction column", () => {
    expect(movement({ price_change: -16, price_direction: null })).toBe("down");
    expect(movement({ price_change: 15, price_direction: null })).toBe("up");
  });

  it("treats 不比價 as no comparison, whatever the number says", () => {
    expect(movement({ price_change: 3.5, price_direction: "X" })).toBe("unmatched");
  });

  it("is flat when there is no change to read", () => {
    expect(movement(undefined)).toBe("flat");
    expect(movement({ price_change: null, price_direction: null })).toBe("flat");
  });
});

describe("changeText", () => {
  it("keeps the published sign", () => {
    expect(changeText({ price_change: -15, price_direction: null })).toBe("−15.00");
    expect(changeText({ price_change: 2, price_direction: "+" })).toBe("+2.00");
    expect(changeText({ price_change: 0, price_direction: " " })).toBe("0.00");
  });

  it("labels 不比價 and a missing change", () => {
    expect(changeText({ price_change: 3.5, price_direction: "X" })).toBe("3.50（不比價）");
    expect(changeText({ price_change: null, price_direction: null })).toBe("—");
  });
});
