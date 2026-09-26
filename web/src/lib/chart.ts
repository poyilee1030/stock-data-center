// The stock page's chart as an ECharts option, built from aligned API rows.
// Pure: the same rows give the same option, and every drawn value is a field
// of an API row (ADR-0029 §6). A missing value is drawn as nothing — "-" for
// candles and bars, null for lines — never as zero.
import type { EChartsOption } from "echarts";
import type { AdjustedRow, AdjustmentEvent, IndicatorRow, MaWindow, PriceRow } from "../api/types";
import { column } from "./series";
import { compact, priceText } from "./format";
import { movement } from "./movement";

export type PriceMode = "raw" | "adjusted";
export type IndicatorKind = "kd" | "rsi" | "macd";

export interface Palette {
  up: string;
  down: string;
  flat: string;
  text: string;
  muted: string;
  grid: string;
  surface: string;
  accent: string;
  ma: Record<MaWindow, string>;
  bollinger: string;
  k: string;
  d: string;
  rsi6: string;
  rsi12: string;
  dif: string;
  dea: string;
}

export interface ChartInput {
  axis: string[];
  prices: (PriceRow | undefined)[];
  indicators: (IndicatorRow | undefined)[];
  adjusted: (AdjustedRow | undefined)[] | null;
  events: AdjustmentEvent[];
  mode: PriceMode;
  ma: MaWindow[];
  bollinger: boolean;
  indicator: IndicatorKind;
  palette: Palette;
  // The visible window, kept across rebuilds; the whole axis when absent.
  window?: { start: string; end: string };
}

type Ohlc = { open: number | null; high: number | null; low: number | null; close: number | null };

function ohlc(input: ChartInput): Ohlc[] {
  if (input.mode === "adjusted") {
    return (input.adjusted ?? input.axis.map(() => undefined)).map((r) => ({
      open: r?.adjusted_open_price ?? null, high: r?.adjusted_high_price ?? null,
      low: r?.adjusted_low_price ?? null, close: r?.adjusted_close_price ?? null,
    }));
  }
  return input.prices.map((r) => ({
    open: r?.open_price ?? null, high: r?.high_price ?? null,
    low: r?.low_price ?? null, close: r?.close_price ?? null,
  }));
}

function candle(bar: Ohlc): [number, number, number, number] | "-" {
  const { open, close, low, high } = bar;
  if (open === null || close === null || low === null || high === null) return "-";
  return [open, close, low, high];  // ECharts' order
}

function movementColor(row: PriceRow, palette: Palette): string {
  const way = movement(row);
  return way === "up" ? palette.up : way === "down" ? palette.down : palette.flat;
}

const GRIDS = [
  { left: 64, right: 16, top: 28, height: "52%" },
  { left: 64, right: 16, top: "63%", height: "11%" },
  { left: 64, right: 16, top: "78%", height: "13%" },
];

function line(id: string, name: string, data: (number | null)[], color: string, index = 0) {
  return {
    id, name, type: "line" as const, data, xAxisIndex: index, yAxisIndex: index,
    showSymbol: false, connectNulls: false, lineStyle: { width: 1.5, color }, itemStyle: { color },
    emphasis: { disabled: true },
  };
}

function indicatorSeries(input: ChartInput) {
  const rows = input.indicators, p = input.palette;
  switch (input.indicator) {
    case "kd":
      return [line("k", "K", column(rows, "k"), p.k, 2), line("d", "D", column(rows, "d"), p.d, 2)];
    case "rsi":
      return [line("rsi6", "RSI6", column(rows, "rsi6"), p.rsi6, 2),
              line("rsi12", "RSI12", column(rows, "rsi12"), p.rsi12, 2)];
    case "macd":
      return [
        {
          id: "macd_hist", name: "MACD 柱", type: "bar" as const, xAxisIndex: 2, yAxisIndex: 2,
          data: column(rows, "macd_hist").map((v) => v === null ? "-" as const
            : { value: v, itemStyle: { color: v >= 0 ? p.up : p.down } }),
        },
        line("macd_dif", "DIF", column(rows, "macd_dif"), p.dif, 2),
        line("macd_dea", "DEA", column(rows, "macd_dea"), p.dea, 2),
      ];
  }
}

function tooltip(input: ChartInput, bars: Ohlc[]) {
  const p = input.palette;
  return (params: unknown): string => {
    const list = Array.isArray(params) ? params : [params];
    const index = (list[0] as { dataIndex?: number } | undefined)?.dataIndex;
    if (index === undefined) return "";
    const bar = bars[index], row = input.prices[index];
    const cell = (label: string, value: string) =>
      `<tr><td style="color:${p.muted};padding-right:12px">${label}</td><td style="text-align:right">${value}</td></tr>`;
    const head = `<div style="font-weight:600;margin-bottom:4px">${input.axis[index]}${
      input.mode === "adjusted" ? `<span style="color:${p.muted};font-weight:400"> · 還原</span>` : ""}</div>`;
    if (!row && bar.close === null) return `${head}<div style="color:${p.muted}">這天沒有資料</div>`;
    return `${head}<table>${cell("開", priceText(bar.open))}${cell("高", priceText(bar.high))}${
      cell("低", priceText(bar.low))}${cell("收", priceText(bar.close))}${
      cell("量（股）", compact(row?.volume ?? null))}</table>`;
  };
}

export function buildChartOption(input: ChartInput): EChartsOption {
  const p = input.palette;
  const bars = ohlc(input);
  const adjusted = input.mode === "adjusted";
  const onAxis = new Set(input.axis);

  const candles = {
    id: "candles", name: adjusted ? "還原 K 線" : "K 線", type: "candlestick" as const,
    data: bars.map(candle), xAxisIndex: 0, yAxisIndex: 0,
    // 台股慣例紅漲綠跌: ECharts' "color" is the rising candle.
    itemStyle: { color: p.up, color0: p.down, borderColor: p.up, borderColor0: p.down },
    barMaxWidth: 12,
    ...(adjusted ? {
      markLine: {
        symbol: ["none", "none"], silent: false, animation: false,
        lineStyle: { color: p.accent, width: 1, type: "solid" as const, opacity: 0.7 },
        label: { color: p.accent, position: "insideEndTop" as const, fontSize: 11 },
        data: input.events.filter((e) => onAxis.has(e.ex_date)).map((e) => ({
          xAxis: e.ex_date, name: e.event_type, label: { formatter: e.event_type },
        })),
      },
    } : {}),
  };

  const overlays = adjusted ? [] : [
    ...input.ma.map((w) => line(`ma${w}`, `MA${w}`, column(input.indicators, `ma${w}`), p.ma[w])),
    ...(input.bollinger ? [
      line("bb_upper", "布林上軌", column(input.indicators, "bb_upper"), p.bollinger),
      line("bb_middle", "布林中軌", column(input.indicators, "bb_middle"), p.bollinger),
      line("bb_lower", "布林下軌", column(input.indicators, "bb_lower"), p.bollinger),
    ] : []),
  ];

  const volume = {
    id: "volume", name: "成交量", type: "bar" as const, xAxisIndex: 1, yAxisIndex: 1,
    barMaxWidth: 12,
    data: input.prices.map((r) => (r === undefined || r.volume === null ? "-" as const
      : { value: r.volume, itemStyle: { color: movementColor(r, p) } })),
  };

  const axisLabel = { color: p.muted, fontSize: 11 };
  const xAxis = GRIDS.map((_, i) => ({
    type: "category" as const, data: input.axis, gridIndex: i, boundaryGap: true,
    axisLine: { lineStyle: { color: p.grid } }, axisTick: { show: false },
    axisLabel: { ...axisLabel, show: i === 2 }, splitLine: { show: false },
    axisPointer: { label: { show: i === 2 } },
  }));
  const yAxis = GRIDS.map((_, i) => ({
    type: "value" as const, gridIndex: i, scale: true, splitNumber: i === 0 ? 5 : 2,
    axisLabel: { ...axisLabel, formatter: i === 1 ? (v: number) => compact(v).replace(/\.0+ /, " ") : undefined },
    splitLine: { lineStyle: { color: p.grid, width: 1 } }, axisLine: { show: false },
  }));
  const zoom = input.window
    ? { startValue: input.window.start, endValue: input.window.end }
    : { start: 0, end: 100 };

  return {
    animation: false,
    backgroundColor: "transparent",
    textStyle: { color: p.text, fontFamily: "inherit" },
    grid: GRIDS,
    xAxis, yAxis,
    axisPointer: { link: [{ xAxisIndex: "all" }], lineStyle: { color: p.muted, width: 1 },
                   label: { backgroundColor: p.surface, color: p.text, borderColor: p.grid } },
    tooltip: {
      trigger: "axis", axisPointer: { type: "cross" }, confine: true,
      backgroundColor: p.surface, borderColor: p.grid, textStyle: { color: p.text, fontSize: 12 },
      formatter: tooltip(input, bars),
    },
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1, 2], ...zoom },
      { type: "slider", xAxisIndex: [0, 1, 2], bottom: 4, height: 18, ...zoom,
        borderColor: p.grid, textStyle: { color: p.muted }, fillerColor: "rgba(128,128,128,0.15)",
        dataBackground: { lineStyle: { color: p.muted }, areaStyle: { color: p.grid } } },
    ],
    series: [candles, ...overlays, volume, ...indicatorSeries(input)],
  } as EChartsOption;
}
