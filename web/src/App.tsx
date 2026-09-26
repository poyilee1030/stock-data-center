import { useCallback, useEffect, useMemo, useState } from "react";
import { api, KeyRequired, storedKey, storeKey } from "./api/client";
import { Key, Logo, Moon, Sun } from "./components/Icons";
import { KeyDialog } from "./components/KeyDialog";
import { SearchBox } from "./components/SearchBox";
import { Home } from "./pages/Home";
import { StockPage } from "./pages/StockPage";
import { useRoute } from "./lib/router";
import { useTheme } from "./lib/theme";
import { useAsync } from "./lib/useAsync";

export default function App() {
  const [theme, toggleTheme] = useTheme();
  const route = useRoute();
  const [keyState, setKeyState] = useState<"ok" | "missing" | "rejected" | "changing">(storedKey() ? "ok" : "missing");
  const [session, setSession] = useState(0);
  const ready = keyState === "ok" || keyState === "changing";

  const stocks = useAsync(ready ? (s) => api.stocks(s) : null, [ready, session]);
  const calendar = useAsync(ready ? (s) => api.tradingDays(s) : null, [ready, session]);
  const [failure, setFailure] = useState<string | null>(null);

  const onError = useCallback((error: Error) => {
    if (error instanceof KeyRequired) setKeyState("rejected");
    else setFailure(error.message);
  }, []);
  useEffect(() => {
    for (const state of [stocks, calendar]) if (state.status === "error") onError(state.error);
  }, [stocks, calendar, onError]);

  const stockList = stocks.status === "done" ? stocks.value.rows : null;
  const days = useMemo(() => (calendar.status === "done" ? calendar.value.rows.map((r) => r.trade_date) : null), [calendar]);
  const byId = useMemo(() => new Map((stockList ?? []).map((s) => [s.stock_id, s])), [stockList]);

  useEffect(() => {
    document.title = route.page === "stock"
      ? `${route.stockId} ${byId.get(route.stockId)?.name ?? ""} · 台股資料中心` : "台股資料中心";
  }, [route, byId]);

  return (
    <>
      <header className="topbar">
        <a className="brand" href="#/"><Logo /><span>台股資料中心</span></a>
        <SearchBox stocks={stockList} />
        <div className="actions">
          <button type="button" className="icon" onClick={toggleTheme} data-testid="theme-toggle"
                  aria-label={theme === "dark" ? "切換到白天模式" : "切換到黑夜模式"}
                  title={theme === "dark" ? "白天模式" : "黑夜模式"}>
            {theme === "dark" ? <Sun /> : <Moon />}
          </button>
          <button type="button" className="icon" onClick={() => setKeyState("changing")} aria-label="更換 API key" title="API key">
            <Key />
          </button>
        </div>
      </header>

      {failure && (
        <div className="banner" role="alert">
          API 回應錯誤：{failure}
          <button type="button" className="ghost" onClick={() => setFailure(null)}>關閉</button>
        </div>
      )}

      <main>
        {!ready ? null : route.page === "home"
          ? <Home stocks={stockList} />
          : <StockPage key={`${route.stockId}:${session}`} stockId={route.stockId} stock={byId.get(route.stockId)}
                       calendar={days} theme={theme} onError={onError} />}
      </main>

      <footer className="site-footer">
        <span>資料來源：證交所、櫃買中心等官方公開資料，經 stock-data-center 公開 API 提供。</span>
        <span>股票清單：今天的上市櫃普通股，加上 2020 年後下市者；資料集只收今天仍上市櫃的股票（存活者偏差）。</span>
      </footer>

      {keyState !== "ok" && (
        <KeyDialog rejected={keyState === "rejected"}
                   onCancel={keyState === "changing" ? () => setKeyState("ok") : undefined}
                   onSave={(key) => { storeKey(key); setKeyState("ok"); setSession((n) => n + 1); }} />
      )}
    </>
  );
}
