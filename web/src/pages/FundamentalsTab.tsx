import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ActionRow, Pit, RevenueRow } from "../api/types";
import { PanelChart } from "../components/PanelChart";
import { PitNote } from "../components/PitNote";
import type { RangeCommand } from "../components/PriceChart";
import { Segmented } from "../components/Segmented";
import { sourceLabel } from "../components/labels";
import type { Palette } from "../lib/chart";
import type { Exchange } from "../lib/exchange";
import { rowsOfExchange } from "../lib/exchange";
import { compact, MISSING, taipei } from "../lib/format";
import {
  ACTION_TERMS, actionCells, REPORT_ITEMS, reportRows, reportSeries, revenuePanels, sortedActions, valuationPanels,
  type ReportMode, type ValuationPanel,
} from "../lib/fundamentals";
import { unitText } from "../lib/panel";
import { useAsync } from "../lib/useAsync";

const MONTHLY_RANGE: RangeCommand = { months: 60, nonce: 0 };
const QUARTERLY_RANGE: RangeCommand = { months: null, nonce: 0 };

// Corporate-action terms keep every published digit: ratios have up to twelve places.
function exact(value: number): string {
  return value.toLocaleString("en-US", { maximumFractionDigits: 12 });
}

function pct(value: number | null | undefined): string {
  return value === null || value === undefined ? MISSING : `${value.toFixed(2)}%`;
}

function Revenue({ rows, pit, palette }: { rows: RevenueRow[]; pit: Pit; palette: Palette }) {
  const built = useMemo(() => revenuePanels(rows, palette), [rows, palette]);
  const bars = useMemo(() => ({ axis: built.axis, series: built.revenue, unit: "twd" as const, palette }), [built, palette]);
  const lines = useMemo(() => ({ axis: built.axis, series: built.growth, unit: "percent" as const, palette }), [built, palette]);
  const latest = [...rows].sort((a, b) => b.revenue_month.localeCompare(a.revenue_month)).slice(0, 12);
  return (
    <section className="card" data-testid="section-revenue">
      <h2 className="card-title">月營收</h2>
      <p className="note">月增率、年增率與累計年增率都是公司發布的值，網頁不另外計算。沒有公開時間證明的月份（例如部分 KY 公司）在 latest 下看不到，圖上留空。</p>
      {rows.length === 0 ? <div className="panel-empty">沒有這檔股票的月營收資料。</div> : (
        <>
          <div className="panel-grid">
            <PanelChart id="revenue" input={bars} group="revenue" range={MONTHLY_RANGE} height={240} />
            <PanelChart id="revenue-growth" input={lines} group="revenue" range={MONTHLY_RANGE} height={240} />
          </div>
          <div className="table-wrap">
            <table className="data compact" data-testid="revenue-table">
              <thead><tr><th>月份</th><th className="num">營收（元）</th><th className="num">上月</th><th className="num">去年同月</th>
                <th className="num">月增率</th><th className="num">年增率</th><th className="num">累計營收</th><th className="num">累計年增率</th><th>備註</th></tr></thead>
              <tbody>
                {latest.map((r) => (
                  <tr key={r.revenue_month}>
                    <td>{r.revenue_month.slice(0, 7)}</td><td className="num">{compact(r.revenue ?? null)}</td>
                    <td className="num">{compact(r.revenue_last_month ?? null)}</td><td className="num">{compact(r.revenue_last_year_month ?? null)}</td>
                    <td className="num">{pct(r.mom_pct)}</td><td className="num">{pct(r.yoy_pct)}</td>
                    <td className="num">{compact(r.cumulative_revenue ?? null)}</td><td className="num">{pct(r.cumulative_yoy_pct)}</td>
                    <td className="wrap">{r.note ?? MISSING}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <footer className="captions"><PitNote pit={pit} what={`monthly-revenues · ${[...new Set(rows.map((r) => r.source))].join("、") || MISSING}`} /></footer>
    </section>
  );
}

function Reports({ stockId, industry, palette, onError }: {
  stockId: string;
  industry: string | null;
  palette: Palette;
  onError: (error: Error) => void;
}) {
  const answer = useAsync((s) => api.reports(stockId, REPORT_ITEMS.map((i) => i.code), s), [stockId]);
  const [mode, setMode] = useState<ReportMode>("ytd");
  useEffect(() => {
    if (answer.status === "error") onError(answer.error);
  }, [answer, onError]);
  const reports = answer.status === "done" ? answer.value.rows : null;
  const lines = useMemo(() => (reports ? reportRows(reports, mode) : []), [reports, mode]);
  const eps = useMemo(() => {
    if (!reports) return null;
    const built = reportSeries(reports, mode, "9750", palette);
    return { axis: built.axis, series: built.series, unit: "per_share" as const, palette };
  }, [reports, mode, palette]);
  return (
    <section className="card" data-testid="section-reports">
      <div className="card-head">
        <h2 className="card-title">財報重點</h2>
        <Segmented label="期間" testId="report-mode" value={mode} onChange={setMode}
                   options={[{ value: "ytd", label: "累計" }, { value: "quarter", label: "單季" }]} />
      </div>
      <p className="note">
        每份財報自己那一期的數字，照公司申報的值。{mode === "quarter"
          ? "第四季的年報只申報全年，沒有單季數字，所以第四季留空（網頁不以全年減前三季自己算）。"
          : "累計是當年一月起到該季底。"}
      </p>
      {answer.status === "loading" ? <div className="panel-empty">載入中…</div>
        : reports && reports.length === 0 ? (
          <div className="panel-empty">
            沒有這檔股票的財報資料。{industry?.includes("金融") ? "金融業的財報不在 v1 範圍（Step 23）。" : ""}
          </div>
        ) : eps && (
          <>
            <PanelChart id={`eps-${mode}`} input={eps} range={QUARTERLY_RANGE} height={220} />
            <div className="table-wrap">
              <table className="data compact" data-testid="report-table">
                <thead><tr><th>季度</th>{REPORT_ITEMS.map((i) => <th key={i.code} className="num" title={i.code}>{i.name}</th>)}<th>報表</th><th>公開</th></tr></thead>
                <tbody>
                  {lines.slice(0, 12).map((l) => (
                    <tr key={l.label}>
                      <td>{l.label}</td>
                      {REPORT_ITEMS.map((i) => (
                        <td key={i.code} className="num" title={l.missingQuarter ? "年報只申報全年" : undefined}>
                          {l.missingQuarter ? "僅全年" : i.unit === "per_share" ? unitText(l.values[i.code], "points") : compact(l.values[i.code])}
                        </td>
                      ))}
                      <td>{l.category === "consolidated" ? "合併" : l.category === "individual" ? "個體" : l.category}</td>
                      <td>{l.availableAt ? taipei(l.availableAt).slice(0, 10) : MISSING}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      <footer className="captions">
        <PitNote pit={answer.status === "done" ? answer.value.pit : undefined} what="financial-reports · income_statement" />
      </footer>
    </section>
  );
}

function ValuationCard({ panel, axis, palette, range, pit, formula }: {
  panel: ValuationPanel;
  axis: string[];
  palette: Palette;
  range: RangeCommand;
  pit: Pit | undefined;
  formula?: string;
}) {
  const input = useMemo(() => ({ axis, series: panel.series, unit: panel.unit, palette }), [axis, panel, palette]);
  const empty = panel.series.every((s) => (s.values ?? []).every((v) => v === null));
  return (
    <section className="card panel-card" data-testid={`panel-${panel.id}`}>
      <h2 className="card-title">{panel.title}</h2>
      {panel.note && <p className="note">{panel.note}</p>}
      {empty ? <div className="panel-empty">這個資料集沒有這檔股票在此市場的資料。</div>
        : <PanelChart id={panel.id} input={input} group="valuation" range={range} />}
      <footer className="captions">
        <PitNote pit={pit} what={`${panel.dataset} · ${panel.sources.join("、") || MISSING}`} />
        {formula && <details className="pit"><summary>定義</summary>{formula}</details>}
      </footer>
    </section>
  );
}

function Actions({ rows, pit }: { rows: ActionRow[]; pit: Pit }) {
  const sorted = sortedActions(rows);
  return (
    <section className="card" data-testid="section-actions">
      <h2 className="card-title">公司行動</h2>
      <p className="note">交易所執行的除權息、減資、面額變更，照結果檔發布的條件。「·」是這個來源不發布的欄位，「—」是這一筆沒有值。</p>
      {sorted.length === 0 ? <div className="panel-empty">沒有公司行動紀錄。</div> : (
        <div className="table-wrap">
          <table className="data compact" data-testid="actions-table">
            <thead><tr><th>除權息日</th><th>類型</th><th>來源</th>{ACTION_TERMS.map((t) => <th key={t.key} className="num">{t.name}</th>)}</tr></thead>
            <tbody>
              {sorted.map((r) => {
                const cells = actionCells(r);
                return (
                  <tr key={`${r.source}:${r.ex_date}`}>
                    <td>{r.ex_date}</td><td>{r.event_type}</td><td><code>{r.source}</code></td>
                    {ACTION_TERMS.map((t) => {
                      const cell = cells[t.key];
                      return (
                        <td key={t.key} className="num" title={cell.kind === "unsourced" ? "此來源不發布" : undefined}>
                          {cell.kind === "value" ? exact(cell.value as number) : cell.kind === "empty" ? MISSING : "·"}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <footer className="captions"><PitNote pit={pit} what="corporate-actions" /></footer>
    </section>
  );
}

export function FundamentalsTab({ stockId, industry, exchange, calendar, palette, range, onError }: {
  stockId: string;
  industry: string | null;
  exchange: Exchange;
  calendar: string[] | null;
  palette: Palette;
  range: RangeCommand;
  onError: (error: Error) => void;
}) {
  const loaded = useAsync((s) => Promise.all([
    api.revenues(stockId, s), api.officialValuations(stockId, s), api.valuationMetrics(stockId, s),
    api.corporateActions(stockId, s),
  ]), [stockId]);
  useEffect(() => {
    if (loaded.status === "error") onError(loaded.error);
  }, [loaded, onError]);
  const valuation = useMemo(() => {
    if (loaded.status !== "done" || calendar === null) return null;
    return valuationPanels(loaded.value[1].rows, loaded.value[2].rows, calendar, exchange, palette);
  }, [loaded, calendar, exchange, palette]);

  if (loaded.status === "error") return <div className="card empty">基本面資料讀取失敗：{loaded.error.message}</div>;
  if (loaded.status !== "done") return <section className="card"><div className="panel-empty">載入中…</div></section>;
  const [revenues, official, metrics, actions] = loaded.value;
  return (
    <div className="chips-tab">
      <Revenue rows={rowsOfExchange(revenues.rows, exchange)} pit={revenues.pit} palette={palette} />
      <Reports stockId={stockId} industry={industry} palette={palette} onError={onError} />
      <h2 className="section-title">估值 · 官方發布與資料中心計算分開（{sourceLabel(exchange === "twse" ? "twse_mi_index" : "tpex_otc_quotes")}）</h2>
      <div className="panel-grid">
        {valuation?.panels.map((panel) => (
          <ValuationCard key={panel.id} panel={panel} axis={valuation.axis.dates} palette={palette} range={range}
                         pit={panel.dataset === "official-valuations" ? official.pit : metrics.pit}
                         formula={panel.dataset === "valuation-metrics" ? metrics.derivation?.formula : undefined} />
        ))}
      </div>
      <Actions rows={actions.rows} pit={actions.pit} />
    </div>
  );
}
