# ADR-0029：網頁儀表板——React + ECharts，由 API 容器同源提供

狀態：**Accepted**（2026-09-26 owner 決定框架、圖表庫交由實作選、原始／還原價切換與主題），
ROADMAP Step 37 實作。補 CLAUDE.md §2 的技術棧：原本只有後端，沒有前端。

## 背景

Step 37 要一個在瀏覽器裡看資料的前端。ROADMAP Step 37「開工前要定」列了四件事：
框架與圖表庫、怎麼提供網頁、瀏覽器怎麼拿 API key、端到端測試工具。

owner 2026-09-26：

- 版面自己設計，不照抄 FinMind（當初提它只是給概念）。
- 框架用 React，或不用框架、純 TypeScript + CSS；圖表庫由實作挑一個好用的。
- 個股頁加「原始價／還原價」切換（Step 36 的 `adjusted-prices-pit` 已上線；原本 ROADMAP 把還原價格列為範圍外，
  那是 Step 36 還不存在時寫的）。
- 白天／黑夜主題切換，預設黑夜。

## 決策

### 1. 框架：React 19 + TypeScript，Vite 建置成靜態檔

三個頁面（首頁、個股、37-b 的市場頁）各有搜尋、切換、圖表之間的聯動（游標停在某日，
旁邊的當日行情跟著換）。純 TypeScript 做得到，但要自己寫狀態與重繪；React 是最常見、
最好找人接手的選擇。路由用 hash（`#/stock/2330`）自己寫十幾行，不另裝路由套件：
hash 路由不需要伺服器為每個路徑回 `index.html`。資料抓取用 `fetch` 加一個小 hook，
不裝 react-query。執行期依賴只有 `react`、`react-dom`、`echarts`。

### 2. 圖表：Apache ECharts 6

K 線、成交量、折線、長條、面積、熱度圖都在同一個庫裡，37-b 的籌碼、37-c 的營收與財報
也用得到；多個 grid 共用一條 x 軸、`dataZoom` 縮放、十字游標聯動都是內建。
TradingView lightweight-charts 的 K 線更輕，但畫不了 37-b/c 的其他圖，兩個庫並存
不划算。ECharts 以 `echarts/core` 按需載入，只帶用到的圖型與元件。

### 3. 由 `api` 容器同源提供網頁

- Docker 多階段建置：`node:22-slim` 跑 `npm ci && npm run build`，Python 映像只帶
  `web/dist` 的成品（`/app/web`），不帶 node。
- `create_app(web_dir=...)`：給了目錄才提供網頁；`python -m stock_data_center.api`
  從 `STOCKDC_WEB_DIR` 讀，容器設成 `/app/web`。沒設就跟以前一樣只有 API。
- 同源，所以不處理 CORS。
- **key 中介層只多放行網頁本身**：`/` 與 `/assets/*`（Vite 產出的檔名帶內容雜湊）。
  `/v1` 照舊要 key，其他路徑照舊 401。網頁不含任何資料，跟 `/docs` 同一個理由。
- `index.html` 回 `Cache-Control: no-cache`，重新部署後瀏覽器一定拿到新版；
  `/assets/*` 帶雜湊，可以長期快取。

### 4. API key：第一次開啟時輸入，存在瀏覽器的 localStorage

- 網頁第一次呼叫 `/v1` 前問 key，存 localStorage；之後每個請求帶 `X-API-Key`。
  回 401 就清掉並再問。
- 限制與 API 本身相同：區網是明文 HTTP，key 在線路上是明文；經 Tailscale 則加密。
  localStorage 對同一個 origin 的任何腳本都可讀，而網頁不載入任何外部腳本（圖表庫
  打包在 `assets` 裡），不從外部 CDN 抓東西——區網或離線的電腦也能用。

### 5. 測試：Vitest 測純函式，Playwright 對真實 API 比對與截圖

- **Vitest**：API 回應 → 圖表資料的轉換都是純函式（`web/src/lib/`），先寫測試。
  重點是「畫出來的值就是回應裡的值」：逐點相等、NULL 保持缺值（不是 0）、
  沒成交的日子在交易日曆 x 軸上留空、不同來源不合併。
- **Playwright**：`scripts/verify_web.sh` 建置網頁、在本機回環位址啟動帶網頁的 API
  （臨時 key），用 Chromium 打開個股頁，從頁面上的 ECharts 實例讀出每個 series 的資料，
  與同一個 API 回應逐點比對；並截深、淺兩個主題與手機寬度的圖給人眼看。
  截圖不等於人眼確認：PR 描述要明講哪些看過。

### 6. 前端不做計算

畫面上的每個數字都是某個 API 回應裡的值（ROADMAP Step 37 邊界）。能做的只有顯示格式：
千分位、以「萬／億」縮寫大數、把 UTC 時間換成台北時間、價格顯示到兩位小數
（還原價格的長小數），比對與 tooltip 用原值。漲跌幅百分比 API 沒有，所以不顯示；
漲跌用來源發布的 `price_change` 與 `price_direction`，成交量柱的紅綠也照
`price_direction`。台股慣例紅漲綠跌。

均線、布林通道、KD、RSI、MACD 是 `technical-indicators`，以**原始收盤價**計算
（它的 `price_adjustment_convention`）。所以切到還原價時，疊在 K 線上的均線與布林
通道隱藏（疊在還原 K 線上會錯位），下方的指標面板標明「以原始收盤價計算」。
還原模式在 K 線上標出套用的公司行動（`adjusted-prices-pit` 的 `events`）。

### 7. 來源不合併、x 軸是交易日曆

- 一檔股票有兩個價格來源時（轉市場，例如 5236 在 2026-07-16 由上櫃轉上市），
  個股頁出現來源切換，預設最新一筆的來源；K 線、指標、還原價都只用選定的來源
  （`adjusted-prices-pit` 在這種情形本來就要求指定 `source`）。
- x 軸是 `/v1/trading-days` 在該來源第一筆到最後一筆價格之間的每個交易日；
  停牌的日子留空，不跳過也不補值。

## 後果

- CLAUDE.md §2 的固定技術棧加上前端一段；`web/` 有自己的 `package.json` 與 lockfile。
- 建置映像需要網路抓 npm 套件；執行期不需要。
- 新增一個 Python 端點以外的服務面：網頁本身公開、資料照舊要 key。
- 歷史時間點選擇器、帳號、寫入都不在 Step 37（ROADMAP）。
