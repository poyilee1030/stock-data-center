// The fundamentals tab: monthly revenue, financial-report highlights,
// valuation and corporate actions. Every value is an API field; nothing is
// computed here (ADR-0029 §6): the published comparatives are drawn as
// published, and a quarter a report does not state is left empty.
import type { ActionRow, OfficialValuationRow, ReportRow, RevenueRow, ValuationMetricsRow } from "../api/types";
import type { Palette } from "./chart";
import { rowsOfExchange, type Exchange } from "./exchange";
import type { PanelSeries, PanelUnit } from "./panel";
import { align, column, tradingAxis, type Axis } from "./series";

// ---------------------------------------------------------------- revenue

/** Every calendar month from the first row's to the last's, as YYYY-MM-01. */
export function monthAxis(rows: { revenue_month: string }[]): string[] {
  if (rows.length === 0) return [];
  const months = rows.map((r) => r.revenue_month).sort();
  const out: string[] = [];
  let [year, month] = months[0].split("-").map(Number);
  const [lastYear, lastMonth] = months[months.length - 1].split("-").map(Number);
  while (year < lastYear || (year === lastYear && month <= lastMonth)) {
    out.push(`${year}-${String(month).padStart(2, "0")}-01`);
    month += 1;
    if (month > 12) { month = 1; year += 1; }
  }
  return out;
}

export function revenuePanels(rows: RevenueRow[], p: Palette): { axis: string[]; revenue: PanelSeries[]; growth: PanelSeries[] } {
  const axis = monthAxis(rows);
  const at = new Map(rows.map((r) => [r.revenue_month, r]));
  if (at.size !== rows.length) throw new Error("two revenue rows for one month: sources are never merged");
  const pick = (field: "revenue" | "yoy_pct" | "mom_pct" | "cumulative_yoy_pct") =>
    axis.map((m) => at.get(m)?.[field] ?? null);
  return {
    axis,
    revenue: [{ id: "revenue", name: "月營收", kind: "bar", color: p.ma[5], values: pick("revenue") }],
    growth: [
      { id: "yoy_pct", name: "年增率", kind: "line", color: p.ma[10], values: pick("yoy_pct") },
      { id: "mom_pct", name: "月增率", kind: "line", color: p.ma[20], values: pick("mom_pct") },
      { id: "cumulative_yoy_pct", name: "累計年增率", kind: "line", color: p.ma[60], values: pick("cumulative_yoy_pct") },
    ],
  };
}

// ---------------------------------------------------------------- financial reports

export type ReportMode = "ytd" | "quarter";

// The income-statement accounts shown, by their TIFRS account codes.
export const REPORT_ITEMS: { code: string; name: string; unit: PanelUnit }[] = [
  { code: "4000", name: "營業收入", unit: "twd" },
  { code: "5900", name: "營業毛利", unit: "twd" },
  { code: "6900", name: "營業利益", unit: "twd" },
  { code: "8200", name: "本期淨利", unit: "twd" },
  { code: "8610", name: "歸屬母公司淨利", unit: "twd" },
  { code: "9750", name: "基本每股盈餘", unit: "per_share" },
];

const QUARTER_START = ["01-01", "04-01", "07-01", "10-01"];
const QUARTER_END = ["03-31", "06-30", "09-30", "12-31"];

export interface ReportLine {
  label: string;
  year: number;
  quarter: number;
  category: string;
  availableAt: string | null;
  values: Record<string, number | null>;
  // The single quarter of a fourth-quarter report: the annual report states
  // only the year.
  missingQuarter: boolean;
}

/** The report's own-period value of each item: its year to date, or its single quarter. */
export function reportRows(reports: ReportRow[], mode: ReportMode): ReportLine[] {
  return [...reports]
    .sort((a, b) => b.report_year - a.report_year || b.report_quarter - a.report_quarter)
    .map((r) => {
      const q = r.report_quarter - 1;
      const end = `${r.report_year}-${QUARTER_END[q]}`;
      const start = mode === "ytd" ? `${r.report_year}-01-01` : `${r.report_year}-${QUARTER_START[q]}`;
      const values: Record<string, number | null> = {};
      for (const item of REPORT_ITEMS) {
        const found = r.facts.filter((f) => f.account_code === item.code && f.period_end === end && f.period_start === start);
        const distinct = new Set(found.map((f) => f.value));
        if (distinct.size > 1) {
          throw new Error(`${r.report_year}Q${r.report_quarter} states ${item.code} twice for ${start}..${end}`);
        }
        values[item.code] = found[0]?.value ?? null;
      }
      return {
        label: `${r.report_year}Q${r.report_quarter}`, year: r.report_year, quarter: r.report_quarter,
        category: r.report_category, availableAt: r.available_at, values,
        missingQuarter: mode === "quarter" && r.report_quarter === 4,
      };
    });
}

export function reportSeries(reports: ReportRow[], mode: ReportMode, code: string, p: Palette):
    { axis: string[]; series: PanelSeries[] } {
  const lines = reportRows(reports, mode).reverse();
  const item = REPORT_ITEMS.find((i) => i.code === code)!;
  return {
    axis: lines.map((l) => l.label),
    series: [{ id: code, name: item.name, kind: "bar", color: p.ma[5], values: lines.map((l) => l.values[code]) }],
  };
}

// ---------------------------------------------------------------- valuation

export interface ValuationPanel {
  id: string;
  title: string;
  dataset: string;
  sources: string[];
  unit: PanelUnit;
  note?: string;
  series: PanelSeries[];
}

export function valuationPanels(official: OfficialValuationRow[], computed: ValuationMetricsRow[], calendar: string[],
                                exchange: Exchange, p: Palette): { axis: Axis; panels: ValuationPanel[] } {
  const o = rowsOfExchange(official, exchange);
  const c = rowsOfExchange(computed, exchange);
  const axis = tradingAxis(calendar, [...o, ...c]);
  const ao = align(axis.dates, o), ac = align(axis.dates, c);
  const sources = (rows: { source: string }[]) => [...new Set(rows.map((r) => r.source))].sort();
  const official_ = "交易所發布的值。";
  const computed_ = "資料中心以 valuation_metrics:v1 計算，不是交易所發布的值。";
  return {
    axis,
    panels: [
      { id: "official-pe-pb", title: "本益比、股價淨值比（官方）", dataset: "official-valuations", sources: sources(o),
        unit: "times", note: official_, series: [
          { id: "pe_ratio", name: "本益比", kind: "line", color: p.ma[5], values: column(ao, "pe_ratio") },
          { id: "pb_ratio", name: "股價淨值比", kind: "line", color: p.ma[10], values: column(ao, "pb_ratio") },
        ] },
      { id: "official-yield", title: "殖利率（官方）", dataset: "official-valuations", sources: sources(o),
        unit: "percent", note: official_, series: [
          { id: "dividend_yield", name: "殖利率", kind: "line", color: p.ma[20], values: column(ao, "dividend_yield") },
        ] },
      { id: "computed-pe", title: "本益比（計算）", dataset: "valuation-metrics", sources: sources(c),
        unit: "times", note: `${computed_}收盤價 ÷ 近四季 EPS，近四季 EPS 不為正時留空。`, series: [
          { id: "pe_ratio", name: "本益比", kind: "line", color: p.ma[5], values: column(ac, "pe_ratio") },
        ] },
      { id: "computed-eps", title: "近四季 EPS（計算）", dataset: "valuation-metrics", sources: sources(c),
        unit: "per_share", note: computed_, series: [
          { id: "ttm_eps", name: "近四季 EPS", kind: "line", color: p.ma[60], values: column(ac, "ttm_eps") },
        ] },
      { id: "computed-roe", title: "ROE（計算）", dataset: "valuation-metrics", sources: sources(c),
        unit: "percent", note: `${computed_}近四季歸屬母公司淨利 ÷ 最新季底歸屬母公司權益。`, series: [
          { id: "roe", name: "ROE", kind: "line", color: p.ma[120], values: column(ac, "roe") },
        ] },
      { id: "computed-percentile", title: "本益比歷史百分位（計算）", dataset: "valuation-metrics", sources: sources(c),
        unit: "percent", note: `${computed_}當日本益比在這檔股票至今所有本益比中的百分位。`, series: [
          { id: "pe_percentile", name: "百分位", kind: "line", color: p.ma[240], values: column(ac, "pe_percentile") },
        ] },
    ],
  };
}

// ---------------------------------------------------------------- corporate actions

export const ACTION_TERMS: { key: string; name: string }[] = [
  { key: "close_before", name: "前一日收盤" },
  { key: "reference_price", name: "參考價" },
  { key: "rights_dividend_value", name: "權值＋息值" },
  { key: "cash_dividend_per_share", name: "現金股利" },
  { key: "free_share_ratio", name: "無償配股率" },
  { key: "rights_ratio", name: "現增認購率" },
  { key: "subscription_price", name: "認購價" },
  { key: "old_shares", name: "舊股數" },
  { key: "new_shares", name: "新股數" },
  { key: "cash_return_per_share", name: "每股退還現金" },
];

export type Cell = { kind: "value"; value: unknown } | { kind: "empty" } | { kind: "unsourced" };

/** A term the API omits is one the source never publishes; a null one it left empty on this row. */
export function actionCells(row: Record<string, unknown>): Record<string, Cell> {
  return Object.fromEntries(ACTION_TERMS.map(({ key }) => [key,
    !(key in row) ? { kind: "unsourced" } : row[key] === null ? { kind: "empty" } : { kind: "value", value: row[key] }]));
}

export function sortedActions(rows: ActionRow[]): ActionRow[] {
  return [...rows].sort((a, b) => b.ex_date.localeCompare(a.ex_date) || a.source.localeCompare(b.source));
}
