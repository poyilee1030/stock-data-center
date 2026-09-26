import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { Pit, RowsAnswer } from "../api/types";
import { PanelChart } from "../components/PanelChart";
import { PitNote } from "../components/PitNote";
import type { RangeCommand } from "../components/PriceChart";
import type { Palette } from "../lib/chart";
import { chipPanels, concentrationPanel, distributionTable, type ChipAnswers, type ChipPanel } from "../lib/chips";
import type { Exchange } from "../lib/exchange";
import { grouped, MISSING } from "../lib/format";
import { useAsync } from "../lib/useAsync";

const GROUP = "chips";

type Loaded = { [K in keyof ChipAnswers]: RowsAnswer<ChipAnswers[K][number]> };

async function loadChips(stockId: string, signal: AbortSignal): Promise<Loaded> {
  const [flows, streaks, cumulative, foreign, margin, marginMetrics, lending] = await Promise.all([
    api.flows(stockId, signal), api.streaks(stockId, signal), api.cumulative(stockId, signal),
    api.foreign(stockId, signal), api.margin(stockId, signal), api.marginMetrics(stockId, signal),
    api.lending(stockId, signal),
  ]);
  return { flows, streaks, cumulative, foreign, margin, marginMetrics, lending };
}

function PanelCard({ panel, axis, palette, range, pit, derived }: {
  panel: ChipPanel;
  axis: string[];
  palette: Palette;
  range: RangeCommand;
  pit: Pit | undefined;
  derived: boolean;
}) {
  const input = useMemo(() => ({ axis, series: panel.series, unit: panel.unit, palette }), [axis, panel, palette]);
  const empty = panel.series.every((s) => (s.values ?? []).every((v) => v === null));
  return (
    <section className="card panel-card" data-testid={`panel-${panel.id}`}>
      <h2 className="card-title">{panel.title}</h2>
      {panel.note && <p className="note">{panel.note}</p>}
      {empty
        ? <div className="panel-empty">這個資料集沒有這檔股票在此市場的資料。</div>
        : <PanelChart id={panel.id} input={input} group={GROUP} range={range} />}
      <footer className="captions">
        <PitNote pit={pit} what={`${panel.dataset} · ${panel.sources.join("、") || MISSING}`} />
        {derived && <span className="pit">衍生資料：latest 輸入</span>}
      </footer>
    </section>
  );
}

const DERIVED = new Set(["institutional-streaks", "institutional-cumulative-flows", "margin-metrics"]);
const PIT_OF: Record<string, keyof Loaded> = {
  "institutional-flows": "flows", "institutional-streaks": "streaks", "institutional-cumulative-flows": "cumulative",
  "foreign-holdings": "foreign", "margin-trading": "margin", "margin-metrics": "marginMetrics",
  "securities-lending": "lending",
};

function Distribution({ stockId, palette, onError }: {
  stockId: string;
  palette: Palette;
  onError: (error: Error) => void;
}) {
  const answers = useAsync((s) => Promise.all([api.concentrations(stockId, s), api.distributions(stockId, s)]), [stockId]);
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => {
    if (answers.status === "error") onError(answers.error);
  }, [answers, onError]);
  const built = useMemo(() => (answers.status === "done" ? concentrationPanel(answers.value[0].rows, palette) : null),
                        [answers, palette]);
  const input = useMemo(() => (built ? { axis: built.axis, series: built.series, unit: "percent" as const, palette } : null),
                        [built, palette]);
  if (answers.status !== "done" || !built || !input) return <section className="card"><div className="panel-empty">載入中…</div></section>;
  const [concentrations, distributions] = answers.value;
  const week = built.axis[hover ?? built.axis.length - 1];
  const row = distributions.rows.find((r) => r.snapshot_date === week);
  const table = row ? distributionTable(row) : null;
  return (
    <section className="card weekly" data-testid="panel-distribution">
      <div className="weekly-chart">
        <h2 className="card-title">股權分散（TDCC 每週）</h2>
        <p className="note">大戶、中實戶、散戶的持股佔比：TDCC 各分級發布的百分比加總（shareholding-concentrations）。游標所在的週，右表列出 15 個持股分級。</p>
        {built.axis.length === 0
          ? <div className="panel-empty">沒有這檔股票的股權分散資料。</div>
          : <PanelChart id="concentration" input={input} range={{ months: 36, nonce: 0 }} onHover={setHover} height={260} />}
        <footer className="captions">
          <PitNote pit={concentrations.pit} what="shareholding-concentrations · tdcc_opendata" />
          <span className="pit">衍生資料：latest 輸入</span>
        </footer>
      </div>
      <div className="weekly-table">
        <div className="day-head"><span className="muted">持股分級</span><span className="day-date">{week ?? MISSING}</span></div>
        {table ? (
          <div className="table-wrap">
            <table className="data compact" data-testid="distribution">
              <thead><tr><th>持股（股）</th><th className="num">人數</th><th className="num">股數</th><th className="num">佔比%</th></tr></thead>
              <tbody>
                {table.levels.map((l) => (
                  <tr key={l.level}><td>{l.range}</td><td className="num">{grouped(l.holders)}</td>
                    <td className="num">{grouped(l.shares)}</td><td className="num">{l.percent === null ? MISSING : l.percent.toFixed(2)}</td></tr>
                ))}
                <tr className="muted-row"><td title="TDCC：因客戶帳戶賣出餘額不足造成的差異，為負數時從合計扣除">差異數調整</td><td className="num">{MISSING}</td>
                  <td className="num">{grouped(table.adjustment.shares)}</td>
                  <td className="num">{table.adjustment.percent === null ? MISSING : table.adjustment.percent.toFixed(2)}</td></tr>
                <tr className="total-row"><td>合計（照來源發布）</td><td className="num">{grouped(table.total.holders)}</td>
                  <td className="num">{grouped(table.total.shares)}</td>
                  <td className="num">{table.total.percent === null ? MISSING : table.total.percent.toFixed(2)}</td></tr>
              </tbody>
            </table>
          </div>
        ) : <p className="muted">這一週沒有分級資料。</p>}
        <footer className="captions"><PitNote pit={distributions.pit} what="shareholding-distributions · tdcc_opendata" /></footer>
      </div>
    </section>
  );
}

export function ChipsTab({ stockId, exchange, calendar, palette, range, onError }: {
  stockId: string;
  exchange: Exchange;
  calendar: string[] | null;
  palette: Palette;
  range: RangeCommand;
  onError: (error: Error) => void;
}) {
  const loaded = useAsync((s) => loadChips(stockId, s), [stockId]);
  useEffect(() => {
    if (loaded.status === "error") onError(loaded.error);
  }, [loaded, onError]);

  const built = useMemo(() => {
    if (loaded.status !== "done" || calendar === null) return null;
    const rows = Object.fromEntries(Object.entries(loaded.value).map(([k, v]) => [k, v.rows])) as unknown as ChipAnswers;
    return chipPanels(rows, calendar, exchange, palette);
  }, [loaded, calendar, exchange, palette]);

  if (loaded.status === "error") return <div className="card empty">籌碼資料讀取失敗：{loaded.error.message}</div>;
  return (
    <div className="chips-tab">
      {built?.axis.offCalendar.length ? (
        <div className="card warn" role="alert">
          有 {built.axis.offCalendar.length} 個日期不在交易日曆上，仍照原樣畫出：{built.axis.offCalendar.slice(0, 5).join("、")}
        </div>
      ) : null}
      <div className="panel-grid">
        {built && loaded.status === "done"
          ? built.panels.map((panel) => (
            <PanelCard key={panel.id} panel={panel} axis={built.axis.dates} palette={palette} range={range}
                       pit={loaded.value[PIT_OF[panel.dataset]].pit} derived={DERIVED.has(panel.dataset)} />
          ))
          : <section className="card"><div className="panel-empty">載入中…</div></section>}
      </div>
      <Distribution stockId={stockId} palette={palette} onError={onError} />
    </div>
  );
}
