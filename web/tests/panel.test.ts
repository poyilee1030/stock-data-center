import { describe, expect, it } from "vitest";
import { buildPanelOption, type PanelInput } from "../src/lib/panel";
import { PALETTE } from "./palette";

const AXIS = ["2024-06-11", "2024-06-12", "2024-06-13"];

function input(over: Partial<PanelInput> = {}): PanelInput {
  return {
    axis: AXIS, unit: "shares", palette: PALETTE,
    series: [
      { id: "foreign_net", name: "外資", kind: "bar", color: "#1", values: [-8251130, 0, null] },
      { id: "held_ratio", name: "外資持股", kind: "line", color: "#2", values: [69.27, null, 69.3] },
    ],
    ...over,
  };
}

function find(option: ReturnType<typeof buildPanelOption>, id: string): any {
  return (option.series as any[]).find((s) => s.id === id);
}

describe("buildPanelOption", () => {
  it("draws every value the API gave, zero included, and leaves a missing one empty", () => {
    const option = buildPanelOption(input());
    expect(find(option, "foreign_net").data).toEqual([-8251130, 0, "-"]);
    expect(find(option, "held_ratio").data).toEqual([69.27, null, 69.3]);
    expect(find(option, "held_ratio").connectNulls).toBe(false);
  });

  it("puts every series on one axis: never a second y scale", () => {
    const option = buildPanelOption(input());
    expect((option.yAxis as any[]).length).toBe(1);
    expect((option.xAxis as any[])[0].data).toEqual(AXIS);
  });

  it("colours a series by what it is, not by its rank", () => {
    const option = buildPanelOption(input());
    expect(find(option, "foreign_net").itemStyle.color).toBe("#1");
    expect(find(option, "held_ratio").lineStyle.color).toBe("#2");
  });

  it("draws a step series as steps and candles from open, close, low, high", () => {
    const option = buildPanelOption(input({
      unit: "points",
      series: [
        { id: "streak", name: "連續天數", kind: "step", color: "#3", values: [3, -1, null] },
        { id: "taiex", name: "加權指數", kind: "candle", color: "#4",
          ohlc: [{ open: 1, high: 4, low: 0.5, close: 3 }, null, { open: 2, high: null, low: 1, close: 2 }] },
      ],
    }));
    expect(find(option, "streak").step).toBe("middle");
    expect(find(option, "taiex").data).toEqual([[1, 3, 0.5, 4], "-", "-"]);
  });

  it("always shows a legend for two series or more", () => {
    expect((buildPanelOption(input()).legend as any).show).toBe(true);
    const one = buildPanelOption(input({ series: [input().series[0]] }));
    expect((one.legend as any).show).toBe(false);
  });

  it("keeps the visible window when given one", () => {
    const zoom = (buildPanelOption(input({ window: { start: "2024-06-12", end: "2024-06-13" } })).dataZoom as any[])[0];
    expect([zoom.startValue, zoom.endValue]).toEqual(["2024-06-12", "2024-06-13"]);
  });
});
