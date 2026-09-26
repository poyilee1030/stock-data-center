import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { AdjustedRow, AdjustmentEvent, IndicatorRow, MaWindow, PriceRow, Stock } from "../api/types";
import { MA_WINDOWS } from "../api/types";
import { PitNote } from "../components/PitNote";
import { PriceChart, type RangeCommand } from "../components/PriceChart";
import { Segmented } from "../components/Segmented";
import { marketLabel, sourceLabel } from "../components/labels";
import type { IndicatorKind, PriceMode } from "../lib/chart";
import { compact, grouped, MISSING, priceText, taipei } from "../lib/format";
import { changeText, movement } from "../lib/movement";
import { rememberStock } from "../lib/recent";
import { stockHref, type StockTab } from "../lib/router";
import { exchangeOf } from "../lib/exchange";
import { ChipsTab } from "./ChipsTab";
import { FundamentalsTab } from "./FundamentalsTab";
import { align, bySource, defaultSource, tradingAxis } from "../lib/series";
import { readPalette, type Theme } from "../lib/theme";
import { useAsync } from "../lib/useAsync";

const RANGES: { months: number | null; label: string }[] = [
  { months: 1, label: "1月" }, { months: 3, label: "3月" }, { months: 6, label: "6月" },
  { months: 12, label: "1年" }, { months: 36, label: "3年" }, { months: null, label: "全部" },
];

function stored<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const value = localStorage.getItem(key) as T | null;
    return value !== null && allowed.includes(value) ? value : fallback;
  } catch {
    return fallback;
  }
}

function keep(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // A convenience only.
  }
}



function DayPanel({ date, price, adjusted, indicator, mode, maShown, palette }: {
  date: string | undefined;
  price: PriceRow | undefined;
  adjusted: AdjustedRow | undefined;
  indicator: IndicatorRow | undefined;
  mode: PriceMode;
  maShown: MaWindow[];
  palette: ReturnType<typeof readPalette>;
}) {
  const rows: [string, string][] = mode === "adjusted"
    ? [["還原開盤", priceText(adjusted?.adjusted_open_price ?? null)],
       ["還原最高", priceText(adjusted?.adjusted_high_price ?? null)],
       ["還原最低", priceText(adjusted?.adjusted_low_price ?? null)],
       ["還原收盤", priceText(adjusted?.adjusted_close_price ?? null)],
       ["原始收盤", priceText(adjusted?.close_price ?? price?.close_price ?? null)],
       ["累積因子", adjusted?.adjustment_factor == null ? MISSING : adjusted.adjustment_factor.toFixed(6)]]
    : [["開盤", priceText(price?.open_price ?? null)], ["最高", priceText(price?.high_price ?? null)],
       ["最低", priceText(price?.low_price ?? null)], ["收盤", priceText(price?.close_price ?? null)],
       ["漲跌", changeText(price)]];
  const trade: [string, string][] = [
    ["成交量（股）", compact(price?.volume ?? null)], ["成交金額（元）", compact(price?.trade_value ?? null)],
    ["成交筆數", grouped(price?.trade_count ?? null)],
    ["最後揭示買", `${priceText(price?.last_bid_price ?? null)} / ${grouped(price?.last_bid_volume ?? null)}`],
    ["最後揭示賣", `${priceText(price?.last_ask_price ?? null)} / ${grouped(price?.last_ask_volume ?? null)}`],
  ];
  return (
    <aside className="card day" data-testid="day-panel">
      <div className="day-head">
        <span className="muted">當日</span>
        <span className="day-date">{date ?? MISSING}</span>
      </div>
      {date && !price && <p className="muted">這個交易日沒有這檔股票的價格（停牌或尚未掛牌）。</p>}
      <dl>{rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
      <dl className="sep">{trade.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
      {mode === "raw" && maShown.length > 0 && (
        <dl className="sep">
          {maShown.map((w) => (
            <div key={w}>
              <dt><i className="dot" style={{ background: palette.ma[w] }} />MA{w}</dt>
              <dd>{priceText(indicator?.[`ma${w}`] ?? null)}</dd>
            </div>
          ))}
        </dl>
      )}
      {price && (
        <p className="provenance">
          公開 {taipei(price.available_at)} · 入庫 {taipei(price.recorded_at)}
          <br />raw <code title={price.provenance.raw_sha256 ?? ""}>{price.provenance.raw_sha256?.slice(0, 12) ?? MISSING}</code>
        </p>
      )}
    </aside>
  );
}

function EventsTable({ events }: { events: AdjustmentEvent[] }) {
  if (events.length === 0) return <p className="muted">這段期間沒有套用任何公司行動。</p>;
  return (
    <div className="table-wrap">
      <table className="data" data-testid="events">
        <thead>
          <tr><th>除權息日</th><th>類型</th><th className="num">前一日收盤</th><th className="num">參考價</th><th className="num">因子</th><th>來源</th></tr>
        </thead>
        <tbody>
          {[...events].reverse().map((e) => (
            <tr key={`${e.source}:${e.ex_date}`}>
              <td>{e.ex_date}</td><td>{e.event_type}</td>
              <td className="num">{priceText(e.close_before)}</td><td className="num">{priceText(e.reference_price)}</td>
              <td className="num">{e.factor === null ? MISSING : e.factor.toFixed(6)}</td>
              <td><code>{e.source}</code></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function StockPage({ stockId, tab, stock, calendar, theme, onError }: {
  stockId: string;
  tab: StockTab;
  stock: Stock | undefined;
  calendar: string[] | null;
  theme: Theme;
  onError: (error: Error) => void;
}) {
  const prices = useAsync((s) => api.prices(stockId, s), [stockId]);
  const indicators = useAsync((s) => api.indicators(stockId, s), [stockId]);
  const [mode, setModeState] = useState<PriceMode>(() => stored("stockdc.mode", ["raw", "adjusted"], "raw"));
  const [chosenSource, setSource] = useState<string | null>(null);
  const [indicator, setIndicatorState] = useState<IndicatorKind>(() => stored("stockdc.indicator", ["kd", "rsi", "macd"], "kd"));
  const [ma, setMa] = useState<MaWindow[]>([5, 20, 60]);
  const [bollinger, setBollinger] = useState(false);
  const [range, setRange] = useState<RangeCommand>({ months: 6, nonce: 0 });
  const [hover, setHover] = useState<number | null>(null);

  const setMode = (m: PriceMode) => { setModeState(m); keep("stockdc.mode", m); };
  const setIndicator = (k: IndicatorKind) => { setIndicatorState(k); keep("stockdc.indicator", k); };

  const priceRows = prices.status === "done" ? prices.value.rows : null;
  const groups = useMemo(() => (priceRows ? bySource(priceRows) : new Map<string, PriceRow[]>()), [priceRows]);
  const source = chosenSource !== null && groups.has(chosenSource) ? chosenSource : defaultSource(priceRows ?? []);

  const adjusted = useAsync(tab === "price" && mode === "adjusted" && source
    ? (s) => api.adjusted(stockId, source, s) : null, [stockId, source, mode, tab]);

  useEffect(() => {
    for (const state of [prices, indicators, ...(mode === "adjusted" ? [adjusted] : [])]) {
      if (state.status === "error") onError(state.error);
    }
  }, [prices, indicators, adjusted, mode, onError]);
  useEffect(() => {
    if (stock || priceRows?.length) rememberStock(stockId);
  }, [stock, priceRows, stockId]);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const palette = useMemo(() => readPalette(), [theme]);
  const own = useMemo(() => (source ? groups.get(source) ?? [] : []), [groups, source]);
  const axis = useMemo(() => tradingAxis(calendar ?? [], own), [calendar, own]);
  const ownIndicators = useMemo(() => indicators.status === "done"
    ? indicators.value.rows.filter((r) => r.source === source) : [], [indicators, source]);
  const adjustedReady = mode === "adjusted" && adjusted.status === "done";

  const input = useMemo(() => ({
    axis: axis.dates,
    prices: align(axis.dates, own),
    indicators: align(axis.dates, ownIndicators),
    adjusted: adjustedReady ? align(axis.dates, adjusted.value.rows) : null,
    events: adjustedReady ? adjusted.value.events : [],
    mode: adjustedReady ? mode : "raw" as PriceMode,
    ma, bollinger, indicator, palette,
  }), [axis, own, ownIndicators, adjustedReady, adjusted, mode, ma, bollinger, indicator, palette]);

  const onHover = useCallback((index: number | null) => setHover(index), []);
  const at = hover ?? axis.dates.length - 1;
  const last = own[own.length - 1];

  const loading = prices.status === "loading" || calendar === null;
  const toggleMa = (w: MaWindow) => setMa((list) => list.includes(w) ? list.filter((x) => x !== w)
    : MA_WINDOWS.filter((x) => x === w || list.includes(x)));

  return (
    <div className="stock-page">
      <header className="stock-head">
        <div className="identity">
          <span className="ticker" data-testid="ticker">{stockId}</span>
          <div>
            <h1>{stock?.name ?? (loading ? "…" : "不在股票清單")}</h1>
            <div className="badges">
              {stock && <span className={`badge ${stock.market ?? "gone"}`}>{marketLabel(stock.market)}</span>}
              {stock?.industry && <span className="badge plain">{stock.industry}</span>}
              {stock?.listings.filter((l) => l.listed_on || l.delisted_on).map((l) => (
                <span key={`${l.market}${l.listed_on}${l.delisted_on}`} className="badge plain"
                      title={l.listed_on ? undefined : "上市早於交易所的上市表（2001 年起）"}>
                  {marketLabel(l.market)} {l.delisted_on ? `${l.listed_on ?? "…"} – ${l.delisted_on}` : `${l.listed_on} 起`}
                </span>
              ))}
            </div>
          </div>
        </div>
        {last && (
          <div className={`quote ${movement(last)}`} data-testid="last-quote">
            <span className="close">{priceText(last.close_price)}</span>
            <span className="change">{changeText(last)}</span>
            <span className="muted small">{last.trade_date} 收盤 · {sourceLabel(last.source)}</span>
          </div>
        )}
      </header>

      <nav className="tabs" aria-label="分頁">
        <a href={stockHref(stockId)} className={tab === "price" ? "on" : ""} aria-current={tab === "price" ? "page" : undefined}>價格</a>
        <a href={stockHref(stockId, "chips")} className={tab === "chips" ? "on" : ""} aria-current={tab === "chips" ? "page" : undefined}>籌碼</a>
        <a href={stockHref(stockId, "fundamentals")} className={tab === "fundamentals" ? "on" : ""}
           aria-current={tab === "fundamentals" ? "page" : undefined}>基本面</a>
      </nav>

      <div className="toolbar">
        {tab === "price" && (
          <Segmented label="價格" testId="price-mode" value={mode} onChange={setMode}
                     options={[{ value: "raw", label: "原始價" },
                               { value: "adjusted", label: "還原價", title: "依交易所參考價往回還原，含息（total return）" }]} />
        )}
        {groups.size > 1 && source && (
          <Segmented label="來源" testId="source" value={source} onChange={setSource}
                     options={[...groups.keys()].map((s) => ({ value: s, label: sourceLabel(s), title: s }))} />
        )}
        <div className="chips" role="group" aria-label="期間">
          {RANGES.map((r) => (
            <button key={r.label} type="button" className={range.months === r.months ? "chip on" : "chip"}
                    onClick={() => setRange({ months: r.months, nonce: range.nonce + 1 })}>{r.label}</button>
          ))}
        </div>
      </div>

      {prices.status === "done" && own.length === 0 && (
        <div className="card empty">
          這檔股票沒有任何價格資料。{stock?.market === null
            ? "它已下市：目前各資料集只收今天仍上市櫃的股票（存活者偏差，已下市公司的資料是 Step 38-b）。"
            : ""}
        </div>
      )}
      {tab === "chips" && source && (
        <ChipsTab stockId={stockId} exchange={exchangeOf(source)} calendar={calendar} palette={palette}
                  range={range} onError={onError} />
      )}

      {tab === "fundamentals" && source && (
        <FundamentalsTab stockId={stockId} industry={stock?.industry ?? null} exchange={exchangeOf(source)}
                         calendar={calendar} palette={palette} range={range} onError={onError} />
      )}

      {tab === "price" && axis.offCalendar.length > 0 && (
        <div className="card warn" role="alert">
          有 {axis.offCalendar.length} 個價格日期不在交易日曆上，仍照原樣畫出：{axis.offCalendar.slice(0, 5).join("、")}
        </div>
      )}

      {tab === "price" && <div className="stock-grid">
        <section className="card chart-card">
          <div className="legend-row">
            {mode === "raw" ? (
              <div className="chips" role="group" aria-label="均線">
                {MA_WINDOWS.map((w) => (
                  <button key={w} type="button" className={ma.includes(w) ? "chip on" : "chip"} onClick={() => toggleMa(w)}>
                    <i className="dot" style={{ background: palette.ma[w] }} />MA{w}
                  </button>
                ))}
                <button type="button" className={bollinger ? "chip on" : "chip"} onClick={() => setBollinger(!bollinger)}>
                  <i className="dot" style={{ background: palette.bollinger }} />布林
                </button>
              </div>
            ) : (
              <p className="note">均線與布林通道以原始收盤價計算（<code>technical-indicators</code>），疊在還原 K 線上會錯位，還原模式不顯示。橘線是套用的公司行動。</p>
            )}
            <Segmented label="指標" value={indicator} onChange={setIndicator}
                       options={[{ value: "kd", label: "KD" }, { value: "rsi", label: "RSI" }, { value: "macd", label: "MACD" }]} />
          </div>
          {loading || (mode === "adjusted" && source !== null && adjusted.status === "loading")
            ? <div className="chart chart-loading">載入中…</div>
            : own.length > 0 && <PriceChart input={input} range={range} onHover={onHover} />}
          <footer className="captions">
            <PitNote pit={prices.status === "done" ? prices.value.pit : undefined}
                     what={`daily-prices · ${source ?? MISSING}`} />
            {adjustedReady && (
              <PitNote pit={adjusted.value.pit}
                       what={`${adjusted.value.derivation?.dataset_code}:${adjusted.value.derivation?.derivation_version}`} />
            )}
            <span className="pit">指標面板：technical-indicators:v1，以原始收盤價計算；latest 輸入</span>
          </footer>
        </section>

        <DayPanel date={axis.dates[at]} price={input.prices[at]} indicator={input.indicators[at]}
                  adjusted={input.adjusted?.[at]} mode={input.mode} maShown={ma} palette={palette} />
      </div>}

      {tab === "price" && adjustedReady && (
        <section className="card">
          <h2 className="card-title">套用的公司行動</h2>
          <p className="muted small">
            因子 = 參考價 ÷ 前一日收盤；某日的還原價 = 原始價 × 該日之後到最後一筆價格之間所有因子的乘積（API 算好，網頁只顯示）。
          </p>
          <EventsTable events={adjusted.value.events} />
        </section>
      )}
    </div>
  );
}
