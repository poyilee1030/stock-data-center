# stock-data-center

台股 PIT（point-in-time）資料中心：負責來源資料擷取、原始資料留存
（raw provenance）、PostgreSQL 歷史紀錄、業務修正版本、發布證據
（publication evidence）、PIT 可見性，以及可重複使用的 canonical 衍生資料集。

本專案**不**負責 EPS 模型訓練、選股模型訓練、投資組合研究、策略排名邏輯，
或任何模型專屬的實驗性功能——這些留給下游的 `stock-eps-model` /
`stock-model-selection` 等專案。

最高優先原則：**歷史正確性不應依賴目前的資料庫狀態、呼叫者的自律、衍生計算
的時機、來源匯入順序、猜測的來源身分，或快取的可用性。**

## 專案狀態

本專案採「一個 step = 一個 branch = 一個 PR」的方式逐步交付，權威的
交付清單在 [`ROADMAP.md`](ROADMAP.md)；每個 step 的角色分工、不變量與
邊界寫在 [`CLAUDE.md`](CLAUDE.md)（"Current Step Sequence" 一節有目前的
狀態快照，但 `ROADMAP.md` 永遠是最新且權威的版本）。

資料庫是 schema v2（ADR-0027）：官方代號當身分、一個（股票, 來源, 日期）一列的寬表、
數字改變才新增列、每列指向一次抓取；股票範圍是上市櫃普通股（ADR-0026），各資料集只抓今天
上市中的股票（已接受生存者偏差）。Step 35-d-3 刪除了全部 v1 表與程式，migration 鏈從
一個 baseline 重新開始。

`stockdc_backfill` 已有 2020-01-02 起的真實歷史，逐筆與 legacy `stock_db` 對帳過：
全市場日行情、126 個指數、官方估值、三大法人個股與市場彙總、外資持股、融資融券、借券、
月營收、財務報表（iXBRL 三張表）、TDCC 股權分散、交易所公司行動結果檔，共 16 張表、
33,828,374 列。衍生資料已存表的有 `technical_indicators:v1`、`institutional_streaks:v1`（Step 26-b）與
`institutional_cumulative_flow:v1`（Step 26-c）、`shareholding_concentration:v1`（Step 26-d）、
`margin_metrics:v1` 與 `short_interest_metrics:v1`（Step 26-e）、`valuation_metrics:v1`（Step 26-f），都以最新的
輸入增量計算；即時計算的 PIT 對照組是 `technical_indicators_pit:v1`。Step 26 的七個衍生資料集都已存表。

公開 API（Step 27）：PIT 可見性層（27-a）、十一個觀測資料集的 HTTP 端點（27-b），以及財報、七個存表的衍生資料集、
`technical_indicators_pit:v1`、股票清單與交易日曆（27-c）已完成，說明在 `docs/api.md`。

歷史股票清單（Step 38-a，MERGED #66，ADR-0028）：`stocks` 收 2020 年以後下市、證明為普通股的公司，`listings` 存每段掛牌期間
（上市日、下市日、市場）。已在暫存資料庫以真實來源驗過（2,006 家、2,019 段）；`stockdc_backfill` 要在合併後才執行
migration 與 `python -m stock_data_center.v2.listings`。

還原價格（Step 36，MERGED #67）：`adjusted_prices_pit:v1`，查詢時計算、一次一檔。

網頁儀表板（Step 37-a MERGED #69、37-b MERGED #70、37-c MERGED #71，ADR-0029）：`api` 容器在 `/` 提供網頁（React + ECharts），只透過公開 API 讀資料；
股票搜尋、個股 K 線／成交量／均線／KD・RSI・MACD、原始價／還原價切換、深淺主題（預設黑夜）；37-b 加個股籌碼分頁（法人、外資、融資融券、借券、股權分散）與市場頁（指數、法人彙總）；37-c 加基本面分頁（月營收、財報重點、官方與計算的估值、公司行動）。畫出來的每個值已用
`scripts/verify_web.sh`（Playwright）對同一個 API 回應逐點比對（2330、6488、5236）。
歷史產業分類（Step 39，ADR-0030）：39-a（MERGED #72）把兩個交易所的產業類別調整公告存進 `industry_changes`；39-b（MERGED #73）把依類股查的每日行情存進 `industry_observations`，作為已結束掛牌期間的錨點與上櫃對帳；39-c（MERGED #74）在查詢時推出分類期間，API `industry-classifications`（`date=` 或 `start`/`end`，三個 PIT 參數），`/v1/stocks` 的下市公司帶最後已知產業。已在 `stockdc_backfill` 以需求方的檢查驗過；API 容器已在合併後重新部署（5a2c397）。

下市公司的各資料集（38-b）、排程的前向抓取（Step 28）尚未開始。

已知限制：2025Q4 之前的財報沒有首見證據，真正延遲申報的公司在 Market PIT 下會偏早
（audit §7.6）；月營收的 KY 公司與更正後的值沒有證明的公開時間，`published_at` 為 NULL。

## 技術棧

```text
Python 3.12+
FastAPI + uvicorn（Step 27-b 起，`python -m stock_data_center.api`）
Pydantic 2.x
SQLAlchemy 2.x
Alembic
PostgreSQL 18+
pytest
httpx
Docker / Docker Compose
網頁（web/）：React 19 + TypeScript + Vite、ECharts 6、Vitest、Playwright（ADR-0029）
```

PostgreSQL 是唯一的正確性來源，沒有快取（`CLAUDE.md` §4）。

## 開發環境設定

1. 啟動 PostgreSQL 18（只聽本機 `127.0.0.1:26519`，區網連不到）：

   ```bash
   docker compose up -d postgres
   ```

2. 建立虛擬環境並安裝套件：

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -e ".[dev]"
   ```

3. 複製環境變數範本並依需要調整：

   ```bash
   cp .env.example .env
   ```

4. 執行資料庫遷移到最新版本：

   ```bash
   .venv/bin/python -m alembic upgrade head
   ```

   舊 migration 鏈建出的資料庫（停在 `5c1e8d2a7b90`）不能直接升級，要先用
   `scripts/rebase_to_baseline.py` 轉到 baseline（不加 `--execute` 只列出會刪什麼），
   見 `docs/schema.md`。

## 公開 API

API 是 compose 的 `api` 服務（`Dockerfile`），聽 `0.0.0.0:28617`，讀 `stockdc_backfill`，key 取自 `.env` 的
`STOCKDC_API_KEY`（沒有 key 就不啟動）。建置並啟動（映像帶上它服務的 commit）：

```bash
scripts/api_up.sh
```

區網或 Tailscale 上的 client 呼叫 `http://<這台的區網或 Tailscale IP>:28617`，每個 `/v1` 請求帶 `X-API-Key`；說明在 `docs/api.md`，
瀏覽器開 `/docs` 是 Swagger UI（不用 key 就能看，按 Authorize 貼上 key 才能試打）。
資料庫只聽本機，client 只能經過 API。

## 測試

```bash
.venv/bin/python -m pytest tests/
```

`tests/integration/` 下的測試需要一個可連線的 PostgreSQL 18（見
`tests/conftest.py` 的 `TEST_DATABASE_URL`，預設指向本機 `stockdc`
資料庫）。

## 執行資料匯入

schema v2（ADR-0027）的回補走同一個入口，每個 job 是 `<表>/<來源>`：

```bash
.venv/bin/python -m stock_data_center.v2.backfill --help
.venv/bin/python -m stock_data_center.v2.backfill --job daily_prices/twse_mi_index \
    --start 2026-09-01 --end 2026-09-11 --purpose gap_fill
```

每次抓取在 `fetches` 記一列，原始檔存在 `data/raw/<ab>/<sha256>`；已完成的期間不會再抓。

## 專案結構

```text
src/stock_data_center/
  v2/              schema v2 的寫入路徑、回補、可見性、衍生資料（ADR-0027）
  ingestion/       來源 adapter、observation 型別、iXBRL parser、HTTP fetcher、raw store
  db/              schema v2 的表（`schema_v2.py`）與共用欄位型別
  provenance.py    抓取目的等共用型別
migrations/        Alembic 遷移：一個 baseline（Step 35-d-3）與其後的衍生資料表、掛牌期間（Step 38-a）、產業分類（Step 39）
docs/decisions/    ADR（架構決策紀錄）
docs/step_reports/ 各 step 的驗收報告（含真實資料證據）
tests/
  unit/            不需資料庫的單元測試
  integration/     需要 PostgreSQL 的整合測試（含遷移、PIT、對帳）
```

## 文件索引

- [`ROADMAP.md`](ROADMAP.md)：權威的交付清單、每個 step 的範圍與驗收標準
- [`CLAUDE.md`](CLAUDE.md)：架構不變量、資料網域邊界、開發慣例
- `docs/decisions/`：每個架構決策的 ADR
- `docs/step_reports/`：每個 step 合併時留下的真實驗收證據
- `docs/source_field_audit.md`：每個來源實際提供哪些欄位、單位、起始年份
- `docs/schema.md`、`docs/pit_semantics.md`：schema v2 的表與 PIT 規則

## 已知問題

- legacy `pe_ratio` 有 15 個 TWSE 日期存到別天的檔案（legacy 沒檢查日期與表頭），
  以它為基準對帳時這些日期整日都會不同，詳見
  `docs/step_reports/step-18-c-acceptance-report.md`。
- legacy `institutional_investors` 有 6 個 TWSE 日期存到別天的檔案，另有 130 個日期共
  1,491 列被 legacy 解析器弄壞（欄位錯位、留下 NULL）；2026 年有 4 天 legacy 在交易日
  當晚就抓了尚未定稿的檔案。詳見 `docs/step_reports/step-20-a-acceptance-report.md`。
- legacy `institutional_summary` 有 3 個 TWSE 日期（2022-09-27、2022-10-25、2026-01-23）
  的金額和 TWSE 現在提供的不同，而 legacy 是在結算後才抓的：TWSE 事後改了歷史數字。
  我們存的是現在的值、在 D+1 03:00 可見，這正是 ADR-0020 接受的「更正 look-ahead」。
  詳見 `docs/step_reports/step-20-b-acceptance-report.md`。
- MOPS `t13sa150_otc`（上櫃外資持股）查詢過去的日期時，會用**今天**的證券清單重建。
  v2 的股票範圍本來就是今天的清單（ADR-0026），所以不影響範圍內的股票；上櫃外資持股
  只用 MOPS 這一個來源（ADR-0027）。
- 融資融券的「張」不一定是 1,000 股：TWSE 註明境外 ETF 和外國股票第二上市例外。期間內
  只有 008201（一張 100 股，2020-01-02 → 2022-07-08），列在 adapter 的
  `TWSE_LOT_SHARES`；同一代號在日期範圍外出現時整個檔案失敗。
- 融資使用率可以超過 100%（單日買超限額，隔天才暫停）；v2 不存只有 TPEx 有的使用率。
- 借券（TWT93U、TPEx `margin/sbl`）以「股」發布，不做張 → 股換算。
- MOPS 偶爾回 `502 Bad Gateway`、TWSE 偶爾回 CDN 錯誤頁而不是 JSON：該次抓取記
  `failed` 或 `quarantined`、保留原始檔，下次回補會再抓。
