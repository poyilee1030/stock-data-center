import type { Stock } from "../api/types";
import { SearchBox } from "../components/SearchBox";
import { marketLabel } from "../components/labels";
import { recentStocks } from "../lib/recent";
import { stockHref } from "../lib/router";

const EXAMPLES = ["2330", "2317", "2454", "6488", "5236"];

function StockChip({ stock, stockId }: { stock: Stock | undefined; stockId: string }) {
  return (
    <a className="stock-chip" href={stockHref(stockId)}>
      <span className="code">{stockId}</span>
      <span>{stock?.name ?? ""}</span>
      {stock && <span className={`badge ${stock.market ?? "gone"}`}>{marketLabel(stock.market)}</span>}
    </a>
  );
}

export function Home({ stocks }: { stocks: Stock[] | null }) {
  const byId = new Map((stocks ?? []).map((s) => [s.stock_id, s]));
  const recent = recentStocks();
  return (
    <div className="home">
      <section className="hero">
        <h1>查一檔股票</h1>
        <p className="muted">價格、技術指標與還原價，全部直接讀自資料中心的公開 API；網頁不另外計算任何數字。</p>
        <SearchBox stocks={stocks} large autoFocus />
      </section>
      {recent.length > 0 && (
        <section>
          <h2 className="section-title">最近看過</h2>
          <div className="stock-chips">{recent.map((id) => <StockChip key={id} stockId={id} stock={byId.get(id)} />)}</div>
        </section>
      )}
      <section>
        <h2 className="section-title">從這裡開始</h2>
        <div className="stock-chips">{EXAMPLES.map((id) => <StockChip key={id} stockId={id} stock={byId.get(id)} />)}</div>
      </section>
      <section className="notes">
        <div className="card">
          <h2 className="card-title">資料時點</h2>
          <p>每個圖都用 API 的預設：<b>latest</b>，也就是請求當下已公開、已入庫的值。圖下方標出回應實際解析成的時間點。</p>
        </div>
        <div className="card">
          <h2 className="card-title">範圍與存活者偏差</h2>
          <p>股票清單含 2020 年以後下市的普通股，但各資料集目前只收<b>今天仍上市櫃</b>的股票，所以歷史資料帶存活者偏差。不含 ETF、特別股、TDR 與權證。</p>
        </div>
        <div className="card">
          <h2 className="card-title">缺值就是缺值</h2>
          <p>沒成交的日子、來源沒發布的欄位都留空，不畫成 0；x 軸是交易日曆，停牌的日子保留空位。</p>
        </div>
      </section>
    </div>
  );
}
