// The chip tab's panels, built from API rows of one exchange. Every value is
// an API field placed on the trading-day axis, or missing (ADR-0029 §6).
import type {
  ConcentrationRow, CumulativeRow, DistributionRow, FlowRow, ForeignRow, LendingRow, MarginMetricsRow, MarginRow,
  StreakRow,
} from "../api/types";
import type { Palette } from "./chart";
import { rowsOfExchange, type Exchange } from "./exchange";
import type { PanelSeries, PanelUnit } from "./panel";
import { align, column, tradingAxis, type Axis } from "./series";

export interface ChipAnswers {
  flows: FlowRow[];
  streaks: StreakRow[];
  cumulative: CumulativeRow[];
  foreign: ForeignRow[];
  margin: MarginRow[];
  marginMetrics: MarginMetricsRow[];
  lending: LendingRow[];
}

export interface ChipPanel {
  id: string;
  title: string;
  dataset: string;
  sources: string[];
  unit: PanelUnit;
  note?: string;
  series: PanelSeries[];
}

type Field<Row> = { [K in keyof Row]: Row[K] extends number | null ? K : never }[keyof Row] & string;

function spec<Row extends { trade_date: string; source: string }>(
  id: string, title: string, dataset: string, unit: PanelUnit, rows: Row[], dates: string[],
  fields: { field: Field<Row>; name: string; kind: PanelSeries["kind"]; color: string }[], note?: string,
): ChipPanel {
  const aligned = align(dates, rows);
  return {
    id, title, dataset, unit, note,
    sources: [...new Set(rows.map((r) => r.source))].sort(),
    series: fields.map((f) => ({ id: f.field, name: f.name, kind: f.kind, color: f.color,
                                 values: column(aligned, f.field) as (number | null)[] })),
  };
}

export function chipPanels(answers: ChipAnswers, calendar: string[], exchange: Exchange, p: Palette):
    { axis: Axis; panels: ChipPanel[] } {
  const a = {
    flows: rowsOfExchange(answers.flows, exchange),
    streaks: rowsOfExchange(answers.streaks, exchange),
    cumulative: rowsOfExchange(answers.cumulative, exchange),
    foreign: rowsOfExchange(answers.foreign, exchange),
    margin: rowsOfExchange(answers.margin, exchange),
    marginMetrics: rowsOfExchange(answers.marginMetrics, exchange),
    lending: rowsOfExchange(answers.lending, exchange),
  };
  const axis = tradingAxis(calendar, Object.values(a).flat());
  const d = axis.dates;
  const [foreign, trust, dealer] = [p.ma[5], p.ma[10], p.ma[20]];
  return {
    axis,
    panels: [
      spec("flows", "三大法人買賣超", "institutional-flows", "shares", a.flows, d, [
        { field: "foreign_net", name: "外資（不含外資自營商）", kind: "bar", color: foreign },
        { field: "trust_net", name: "投信", kind: "bar", color: trust },
        { field: "dealer_net", name: "自營商", kind: "bar", color: dealer },
      ]),
      spec("streaks", "法人連續買賣天數", "institutional-streaks", "days", a.streaks, d, [
        { field: "foreign_streak_days", name: "外資", kind: "step", color: foreign },
        { field: "trust_streak_days", name: "投信", kind: "step", color: trust },
        { field: "dealer_streak_days", name: "自營商", kind: "step", color: dealer },
      ], "正數是連續買超天數，負數是連續賣超天數。"),
      spec("cumulative", "法人累積淨買賣佔發行股數", "institutional-cumulative-flows", "percent", a.cumulative, d, [
        { field: "trust_cumulative_net_ratio", name: "投信", kind: "line", color: trust },
        { field: "dealer_cumulative_net_ratio", name: "自營商", kind: "line", color: dealer },
      ], "從序列第一天起累積的淨買賣，是估計值，不是實際持股。"),
      spec("foreign", "外資持股比率", "foreign-holdings", "percent", a.foreign, d, [
        { field: "held_ratio", name: "外資持股", kind: "line", color: foreign },
      ]),
      spec("margin", "融資餘額", "margin-trading", "shares", a.margin, d, [
        { field: "margin_balance", name: "融資餘額", kind: "line", color: p.up },
      ]),
      spec("short", "融券餘額", "margin-trading", "shares", a.margin, d, [
        { field: "short_balance", name: "融券餘額", kind: "line", color: p.down },
      ]),
      spec("usage", "融資融券使用率", "margin-metrics", "percent", a.marginMetrics, d, [
        { field: "margin_usage_ratio", name: "融資使用率", kind: "line", color: p.up },
        { field: "short_usage_ratio", name: "融券使用率", kind: "line", color: p.down },
      ], "餘額 ÷ 限額 × 100。"),
      spec("lending", "借券賣出餘額", "securities-lending", "shares", a.lending, d, [
        { field: "balance", name: "借券賣出餘額", kind: "line", color: p.ma[60] },
      ]),
    ],
  };
}

export function concentrationPanel(rows: ConcentrationRow[], p: Palette): { axis: string[]; series: PanelSeries[] } {
  const sorted = [...rows].sort((x, y) => x.snapshot_date.localeCompare(y.snapshot_date));
  const values = (field: "large_holder_ratio" | "mid_holder_ratio" | "small_holder_ratio") =>
    sorted.map((r) => r[field] ?? null);
  return {
    axis: sorted.map((r) => r.snapshot_date),
    series: [
      { id: "large_holder_ratio", name: "大戶（400 張以上）", kind: "line", color: p.ma[5], values: values("large_holder_ratio") },
      { id: "mid_holder_ratio", name: "中實戶（50–400 張）", kind: "line", color: p.ma[10], values: values("mid_holder_ratio") },
      { id: "small_holder_ratio", name: "散戶（50 張以下）", kind: "line", color: p.ma[20], values: values("small_holder_ratio") },
    ],
  };
}

// TDCC's own labels for holding levels 1-15, in shares, as its 集保戶股權分散表
// query page prints them (the legacy scraper matched them to the level numbers).
export const LEVEL_RANGES = [
  "1-999", "1,000-5,000", "5,001-10,000", "10,001-15,000", "15,001-20,000", "20,001-30,000",
  "30,001-40,000", "40,001-50,000", "50,001-100,000", "100,001-200,000", "200,001-400,000",
  "400,001-600,000", "600,001-800,000", "800,001-1,000,000", "1,000,001以上",
];

type Num = number | null;

export function distributionTable(row: DistributionRow) {
  const n = (key: string): Num => (typeof row[key] === "number" ? (row[key] as number) : null);
  return {
    date: row.snapshot_date,
    levels: LEVEL_RANGES.map((range, i) => ({
      level: i + 1, range, holders: n(`holders_${i + 1}`), shares: n(`shares_${i + 1}`), percent: n(`percent_${i + 1}`),
    })),
    adjustment: { shares: n("adjustment_shares"), percent: n("adjustment_percent") },
    total: { holders: n("total_holders"), shares: n("total_shares"), percent: n("total_percent") },
  };
}
