import { describe, expect, it } from "vitest";
import { query, todayInTaipei } from "../src/api/client";

describe("query", () => {
  it("repeats a list parameter and leaves out absent ones", () => {
    expect(query({ start: "2020-01-01", end: "2026-09-26", stock_id: ["2330", "2317"], source: undefined }))
      .toBe("start=2020-01-01&end=2026-09-26&stock_id=2330&stock_id=2317");
  });

  it("never sends a PIT parameter the caller did not give: latest is the API's default", () => {
    expect(query({ start: "a", end: "b" })).not.toMatch(/as_of/);
  });
});

describe("todayInTaipei", () => {
  it("is the Taipei calendar date, not UTC's", () => {
    expect(todayInTaipei(new Date("2026-09-25T17:30:00Z"))).toBe("2026-09-26");
    expect(todayInTaipei(new Date("2026-09-25T15:30:00Z"))).toBe("2026-09-25");
  });
});
