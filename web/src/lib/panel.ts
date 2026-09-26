// A small time-series panel: bars, lines, steps or candles on one y axis.
// Pure, like chart.ts: every drawn value is an API field or missing, and two
// measures of different scale go in two panels, never on a second axis.
import type { EChartsOption } from "echarts";
import type { Palette } from "./chart";
import { compact, grouped, MISSING, priceText } from "./format";

export type PanelUnit = "shares" | "twd" | "percent" | "days" | "points" | "times" | "per_share";
export type PanelKind = "bar" | "line" | "step" | "candle";

export interface Ohlc {
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
}

export interface PanelSeries {
  id: string;
  name: string;
  kind: PanelKind;
  color: string;
  values?: (number | null)[];
  ohlc?: (Ohlc | null)[];
}

export interface PanelInput {
  axis: string[];
  series: PanelSeries[];
  unit: PanelUnit;
  palette: Palette;
  window?: { start: string; end: string };
}

export function unitText(value: number | null, unit: PanelUnit): string {
  if (value === null) return MISSING;
  switch (unit) {
    case "shares": return `${compact(value)} 股`;
    case "twd": return `${compact(value)} 元`;
    case "percent": return `${value.toFixed(2)}%`;
    case "days": return `${grouped(value)} 天`;
    case "points": return priceText(value);
    case "times": return `${value.toFixed(2)} 倍`;
    case "per_share": return `${priceText(value)} 元`;
  }
}

function axisText(value: number, unit: PanelUnit): string {
  if (unit === "percent") return `${value}%`;
  if (unit === "days" || unit === "points" || unit === "times" || unit === "per_share") return grouped(value);
  return compact(value).replace(/\.0+ /, " ");
}

function candle(bar: Ohlc | null): [number, number, number, number] | "-" {
  if (!bar || bar.open === null || bar.close === null || bar.low === null || bar.high === null) return "-";
  return [bar.open, bar.close, bar.low, bar.high];
}

function drawn(s: PanelSeries, palette: Palette) {
  const common = { id: s.id, name: s.name, emphasis: { disabled: true } };
  if (s.kind === "candle") {
    return { ...common, type: "candlestick" as const, data: (s.ohlc ?? []).map(candle), barMaxWidth: 12,
             itemStyle: { color: palette.up, color0: palette.down, borderColor: palette.up, borderColor0: palette.down } };
  }
  if (s.kind === "bar") {
    return { ...common, type: "bar" as const, barMaxWidth: 10, itemStyle: { color: s.color },
             data: (s.values ?? []).map((v) => (v === null ? "-" as const : v)) };
  }
  return { ...common, type: "line" as const, data: s.values ?? [], showSymbol: false, connectNulls: false,
           ...(s.kind === "step" ? { step: "middle" as const } : {}),
           lineStyle: { width: 1.5, color: s.color }, itemStyle: { color: s.color } };
}

export function buildPanelOption(input: PanelInput): EChartsOption {
  const { palette: p, unit } = input;
  const zoom = input.window ? { startValue: input.window.start, endValue: input.window.end } : { start: 0, end: 100 };
  return {
    animation: false,
    backgroundColor: "transparent",
    textStyle: { color: p.text, fontFamily: "inherit" },
    grid: { left: 64, right: 16, top: 34, bottom: 28 },
    legend: { show: input.series.length > 1, top: 0, left: 0, itemWidth: 12, itemHeight: 8,
              textStyle: { color: p.muted, fontSize: 12 } },
    xAxis: [{ type: "category", data: input.axis, boundaryGap: true, axisTick: { show: false },
              axisLine: { lineStyle: { color: p.grid } }, axisLabel: { color: p.muted, fontSize: 11 } }],
    yAxis: [{ type: "value", scale: unit === "points", splitNumber: 3, axisLine: { show: false },
              splitLine: { lineStyle: { color: p.grid } },
              axisLabel: { color: p.muted, fontSize: 11, formatter: (v: number) => axisText(v, unit) } }],
    tooltip: {
      trigger: "axis", confine: true, axisPointer: { type: "line", lineStyle: { color: p.muted } },
      backgroundColor: p.surface, borderColor: p.grid, textStyle: { color: p.text, fontSize: 12 },
      formatter: (params: unknown) => {
        const list = (Array.isArray(params) ? params : [params]) as { dataIndex: number; seriesId: string; marker: string }[];
        if (list.length === 0) return "";
        const i = list[0].dataIndex;
        const lines = input.series.map((s) => {
          if (s.kind === "candle") {
            const bar = s.ohlc?.[i] ?? null;
            return `${s.name}　開 ${priceText(bar?.open ?? null)} 高 ${priceText(bar?.high ?? null)} 低 ${priceText(bar?.low ?? null)} 收 ${priceText(bar?.close ?? null)}`;
          }
          const dot = `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${s.color};margin-right:6px"></span>`;
          return `${dot}${s.name}　<b>${unitText(s.values?.[i] ?? null, unit)}</b>`;
        });
        return `<div style="font-weight:600;margin-bottom:4px">${input.axis[i]}</div>${lines.join("<br>")}`;
      },
    },
    dataZoom: [{ type: "inside", xAxisIndex: [0], ...zoom }],
    series: input.series.map((s) => drawn(s, p)),
  } as EChartsOption;
}
