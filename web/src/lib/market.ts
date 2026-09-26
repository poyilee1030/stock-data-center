// The market page: indices and the institutional market summary, each source
// apart (CLAUDE.md §30). Every value is an API field or missing.
import type { IndexRow, MarketFlowRow } from "../api/types";
import type { Palette } from "./chart";
import type { PanelSeries } from "./panel";
import { align, column, tradingAxis, type Axis } from "./series";

type OnAxis = IndexRow & { stock_id?: never };

/** Each (source, index) pair's latest row, sorted by source then name. */
export function latestIndices(rows: IndexRow[]): IndexRow[] {
  const latest = new Map<string, IndexRow>();
  for (const row of rows) {
    const key = `${row.source}\u0000${row.index_name}`;
    const seen = latest.get(key);
    if (!seen || row.trade_date > seen.trade_date) latest.set(key, row);
  }
  return [...latest.values()].sort((a, b) => a.source.localeCompare(b.source) || a.index_name.localeCompare(b.index_name));
}

function oneIndex(rows: IndexRow[]): void {
  const keys = new Set(rows.map((r) => `${r.source} ${r.index_name}`));
  if (keys.size > 1) throw new Error(`one index per panel, got ${[...keys].join(", ")}`);
}

export function taiexPanel(rows: IndexRow[], calendar: string[]): { axis: Axis; series: PanelSeries[] } {
  oneIndex(rows);
  const axis = tradingAxis(calendar, rows);
  const aligned = align(axis.dates, rows as OnAxis[]);
  return {
    axis,
    series: [{ id: "taiex", name: rows[0]?.index_name ?? "", kind: "candle", color: "",
               ohlc: aligned.map((r) => (r ? { open: r.open_value ?? null, high: r.high_value ?? null,
                                               low: r.low_value ?? null, close: r.close_value } : null)) }],
  };
}

export function indexPanel(rows: IndexRow[], calendar: string[], p: Palette): { axis: Axis; series: PanelSeries[] } {
  oneIndex(rows);
  const axis = tradingAxis(calendar, rows);
  return {
    axis,
    series: [{ id: "close_value", name: rows[0]?.index_name ?? "", kind: "line", color: p.ma[5],
               values: column(align(axis.dates, rows as OnAxis[]), "close_value") }],
  };
}

// Rows that sum others. They stay in the day table, not among the bars, so a
// bar never stands for a sum of the bars beside it.
export const TOTALS = new Set(["合計", "三大法人合計*", "外資及陸資合計", "自營商合計"]);

// Each institution's colour slot, the same on both exchanges; the names are
// the ones each summary publishes (audit, Step 20-b). A new name is refused.
const INSTITUTIONS: Record<string, 5 | 10 | 20 | 60 | 120> = {
  "外資及陸資(不含外資自營商)": 5, "外資及陸資(不含自營商)": 5,
  "投信": 10,
  "自營商(自行買賣)": 20,
  "外資自營商": 60,
  "自營商(避險)": 120,
};

export function marketFlowPanel(rows: MarketFlowRow[], calendar: string[], p: Palette):
    { axis: Axis; series: PanelSeries[] } {
  const bars = rows.filter((r) => !TOTALS.has(r.institution));
  for (const r of bars) {
    if (!(r.institution in INSTITUTIONS)) throw new Error(`unknown institution ${r.institution} in ${r.source}`);
  }
  const axis = tradingAxis(calendar, rows);
  const names = Object.keys(INSTITUTIONS).filter((name) => bars.some((r) => r.institution === name));
  return {
    axis,
    series: names.map((name) => ({
      id: name, name, kind: "bar" as const, color: p.ma[INSTITUTIONS[name]],
      values: column(align(axis.dates, bars.filter((r) => r.institution === name)), "net"),
    })),
  };
}
