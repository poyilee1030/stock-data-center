import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { IndexRow, MarketFlowRow, RowsAnswer } from "../api/types";
import { PanelChart } from "../components/PanelChart";
import { PitNote } from "../components/PitNote";
import type { RangeCommand } from "../components/PriceChart";
import { Segmented } from "../components/Segmented";
import { compact, MISSING, priceText } from "../lib/format";
import { indexPanel, latestIndices, marketFlowPanel, taiexPanel, TOTALS } from "../lib/market";
import { readPalette, type Theme } from "../lib/theme";
import { useAsync } from "../lib/useAsync";

const RANGES: { months: number | null; label: string }[] = [
  { months: 1, label: "1月" }, { months: 3, label: "3月" }, { months: 6, label: "6月" },
  { months: 12, label: "1年" }, { months: 36, label: "3年" }, { months: null, label: "全部" },
];

const SOURCE_LABEL: Record<string, string> = {
  twse_mi_5mins_hist: "證交所 MI_5MINS_HIST（開高低收）",
  twse_mi_index: "證交所 MI_INDEX",
  tpex_index_summary: "櫃買中心指數",
  twse_bfi82u: "證交所 BFI82U",
  tpex_insti_summary: "櫃買中心",
};

// MI_5MINS_HIST publishes one index, TAIEX, with its open, high and low.
const TAIEX_SOURCE = "twse_mi_5mins_hist";
const DEFAULT_INDEX = { source: "tpex_index_summary", index_name: "指數:櫃買指數" };

function signed(value: number | null | undefined, digits: number): string {
  if (value === null || value === undefined) return MISSING;
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toFixed(digits)}`;
}

function IndexTable({ rows, chosen, onChoose }: {
  rows: IndexRow[];
  chosen: { source: string; index_name: string };
  onChoose: (row: IndexRow) => void;
}) {
  const [source, setSource] = useState("twse_mi_index");
  const shown = rows.filter((r) => r.source === source);
  const sources = [...new Set(rows.map((r) => r.source))].filter((s) => s !== TAIEX_SOURCE);
  return (
    <section className="card index-list">
      <div className="card-head">
        <h2 className="card-title">各指數最新收盤</h2>
        <Segmented label="來源" value={source} onChange={setSource}
                   options={sources.map((s) => ({ value: s, label: SOURCE_LABEL[s] ?? s, title: s }))} />
      </div>
      <p className="note">點一列，右邊畫它的整段收盤走勢。漲跌與漲跌幅是來源發布的值。</p>
      <div className="table-wrap scroll">
        <table className="data compact" data-testid="index-table">
          <thead><tr><th>指數</th><th>日期</th><th className="num">收盤</th><th className="num">漲跌</th><th className="num">漲跌幅%</th></tr></thead>
          <tbody>
            {shown.map((r) => {
              const on = r.source === chosen.source && r.index_name === chosen.index_name;
              const way = (r.change_points ?? 0) > 0 ? "up" : (r.change_points ?? 0) < 0 ? "down" : "";
              return (
                <tr key={r.index_name} className={on ? "on clickable" : "clickable"} onClick={() => onChoose(r)}>
                  <td>{r.index_name}</td><td>{r.trade_date}</td><td className="num">{priceText(r.close_value)}</td>
                  <td className={`num ${way}`}>{signed(r.change_points, 2)}</td>
                  <td className={`num ${way}`}>{signed(r.change_percent, 2)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function FlowDay({ rows, date }: { rows: MarketFlowRow[]; date: string | undefined }) {
  const day = rows.filter((r) => r.trade_date === date);
  return (
    <table className="data compact" data-testid="flow-day">
      <thead><tr><th>{date ?? MISSING}</th><th className="num">買進</th><th className="num">賣出</th><th className="num">買賣差額</th></tr></thead>
      <tbody>
        {day.map((r) => (
          <tr key={r.institution} className={TOTALS.has(r.institution) ? "total-row" : ""}>
            <td>{r.institution}</td><td className="num">{compact(r.buy)}</td><td className="num">{compact(r.sell)}</td>
            <td className={`num ${(r.net ?? 0) > 0 ? "up" : (r.net ?? 0) < 0 ? "down" : ""}`}>{compact(r.net)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function MarketFlows({ answer, calendar, range, palette }: {
  answer: RowsAnswer<MarketFlowRow>;
  calendar: string[];
  range: RangeCommand;
  palette: ReturnType<typeof readPalette>;
}) {
  const source = answer.rows[0]?.source ?? "";
  const built = useMemo(() => marketFlowPanel(answer.rows, calendar, palette), [answer, calendar, palette]);
  const input = useMemo(() => ({ axis: built.axis.dates, series: built.series, unit: "twd" as const, palette }), [built, palette]);
  const [hover, setHover] = useState<number | null>(null);
  const date = built.axis.dates[hover ?? built.axis.dates.length - 1];
  return (
    <section className="card flows" data-testid={`flows-${source}`}>
      <h2 className="card-title">三大法人買賣差額 · {SOURCE_LABEL[source] ?? source}</h2>
      <p className="note">單位元。長條是各類法人；合計列只在右表，照來源發布，不另外加總。</p>
      <div className="flows-body">
        <PanelChart id={`flows-${source}`} input={input} range={range} onHover={setHover} height={240} />
        <div className="table-wrap"><FlowDay rows={answer.rows} date={date} /></div>
      </div>
      <footer className="captions"><PitNote pit={answer.pit} what={`institutional-market-flows · ${source}`} /></footer>
    </section>
  );
}

export function MarketPage({ calendar, theme, onError }: {
  calendar: string[] | null;
  theme: Theme;
  onError: (error: Error) => void;
}) {
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const palette = useMemo(() => readPalette(), [theme]);
  const [range, setRange] = useState<RangeCommand>({ months: 6, nonce: 0 });
  const [chosen, setChosen] = useState(DEFAULT_INDEX);

  const taiex = useAsync((s) => api.indices({ source: TAIEX_SOURCE }, s), []);
  // Every index over the month before TAIEX's last date: enough for each one's
  // latest close, whether or not data reaches today.
  const lastDate = taiex.status === "done" ? taiex.value.rows.at(-1)?.trade_date ?? null : null;
  const recent = useAsync(lastDate ? (s) => {
    const from = new Date(Date.parse(`${lastDate}T00:00:00Z`) - 31 * 86_400_000).toISOString().slice(0, 10);
    return api.indices({ start: from }, s);
  } : null, [lastDate]);
  const picked = useAsync((s) => api.indices(chosen, s), [chosen.source, chosen.index_name]);
  const flows = useAsync((s) => Promise.all([api.marketFlows("twse_bfi82u", s), api.marketFlows("tpex_insti_summary", s)]), []);

  useEffect(() => {
    for (const state of [taiex, recent, picked, flows]) if (state.status === "error") onError(state.error);
  }, [taiex, recent, picked, flows, onError]);

  const taiexBuilt = useMemo(() => (taiex.status === "done" && calendar ? taiexPanel(taiex.value.rows, calendar) : null),
                             [taiex, calendar]);
  const pickedBuilt = useMemo(() => (picked.status === "done" && calendar ? indexPanel(picked.value.rows, calendar, palette) : null),
                              [picked, calendar, palette]);
  const latest = useMemo(() => (recent.status === "done" ? latestIndices(recent.value.rows) : []), [recent]);
  const lastTaiex = taiex.status === "done" ? taiex.value.rows.at(-1) : undefined;

  return (
    <div className="market-page">
      <header className="stock-head">
        <div className="identity"><div><h1>市場</h1><p className="muted small">指數與三大法人市場彙總，各來源分開顯示。</p></div></div>
        {lastTaiex && (
          <div className="quote" data-testid="taiex-quote">
            <span className="close">{priceText(lastTaiex.close_value)}</span>
            <span className="muted small">加權指數 · {lastTaiex.trade_date} 收盤</span>
          </div>
        )}
      </header>
      <div className="toolbar">
        <div className="chips" role="group" aria-label="期間">
          {RANGES.map((r) => (
            <button key={r.label} type="button" className={range.months === r.months ? "chip on" : "chip"}
                    onClick={() => setRange({ months: r.months, nonce: range.nonce + 1 })}>{r.label}</button>
          ))}
        </div>
      </div>

      <div className="panel-grid">
        <section className="card panel-card" data-testid="panel-taiex">
          <h2 className="card-title">{lastTaiex?.index_name ?? "加權指數"}</h2>
          {taiexBuilt
            ? <PanelChart id="taiex" height={300} range={range}
                          input={{ axis: taiexBuilt.axis.dates, series: taiexBuilt.series, unit: "points", palette }} />
            : <div className="panel-empty">載入中…</div>}
          <footer className="captions">
            <PitNote pit={taiex.status === "done" ? taiex.value.pit : undefined} what="indices · twse_mi_5mins_hist" />
          </footer>
        </section>
        <section className="card panel-card" data-testid="panel-index">
          <h2 className="card-title">{chosen.index_name}</h2>
          {pickedBuilt
            ? <PanelChart id="index" height={300} range={range}
                          input={{ axis: pickedBuilt.axis.dates, series: pickedBuilt.series, unit: "points", palette }} />
            : <div className="panel-empty">載入中…</div>}
          <footer className="captions">
            <PitNote pit={picked.status === "done" ? picked.value.pit : undefined} what={`indices · ${chosen.source}`} />
          </footer>
        </section>
      </div>

      <IndexTable rows={latest} chosen={chosen}
                  onChoose={(r) => setChosen({ source: r.source, index_name: r.index_name })} />

      {flows.status === "done" && calendar
        ? flows.value.map((answer, i) => <MarketFlows key={i} answer={answer} calendar={calendar} range={range} palette={palette} />)
        : <section className="card"><div className="panel-empty">載入中…</div></section>}
    </div>
  );
}
