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

已完成並驗證的真實資料（2020-01-02 → 2026-09-11，逐筆與 legacy 對帳）：
全市場日行情（Step 17）、市場指數（Step 18-a/b）、交易所公司行動結果檔
（Step 19）。官方估值（本益比／股價淨值比／殖利率，Step 18-c）的回補與
對帳已完成，正在審查中。

## 技術棧

```text
Python 3.12+
FastAPI（規劃中，API 尚未開放）
Pydantic 2.x
SQLAlchemy 2.x
Alembic
PostgreSQL 18+
pytest
httpx
Docker / Docker Compose
```

Redis 為選配，且要等 Step 30 的延遲量測證實有需要才會啟用；PostgreSQL 是
唯一的正確性來源（`CLAUDE.md` §4）。

## 開發環境設定

1. 啟動 PostgreSQL 18：

   ```bash
   docker compose up -d
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

## 測試

```bash
.venv/bin/python -m pytest tests/
```

`tests/integration/` 下的測試需要一個可連線的 PostgreSQL 18（見
`tests/conftest.py` 的 `TEST_DATABASE_URL`，預設指向本機 `stockdc`
資料庫）。標記為 `live_source` 的測試會呼叫真實官方端點，預設略過，
需要時設定 `RUN_LIVE_SOURCE_TESTS=1` 才會執行。

## 執行資料匯入

匯入指令走同一個 CLI 入口，尚未包裝成套件的 console script：

```bash
.venv/bin/python -m stock_data_center.ingestion.cli --help
```

各子指令（`daily-market`、`market-index`、`official-valuation`、`corporate-action` 等）對應
`ROADMAP.md` 上已交付的資料網域；每個真實來源的匯入/回補都會產生
import manifest（涵蓋範圍、筆數、去重、隔離等統計），並對照 legacy
`stock_db` 做對帳（`CLAUDE.md` §78）。

## 專案結構

```text
src/stock_data_center/
  ingestion/       原始資料擷取（adapter、raw-first 生命週期、CLI）
  db/              資料表 metadata
  evidence/        發布證據政策（ADR-0020）
  market_data/、market_reference/、market_calendar/
                   已上線的業務資料網域
  pit/             point-in-time 解析邏輯
  provenance.py    來源/匯入批次共用型別
migrations/        Alembic 遷移腳本
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

## 已知問題

- legacy `pe_ratio` 有 15 個 TWSE 日期存到別天的檔案（legacy 沒檢查
  日期與表頭），以它為基準對帳時這些日期整日都會不同；已在
  `scripts/reconcile_official_valuation.py` 分類，詳見
  `docs/step_reports/step-18-c-acceptance-report.md`。
- `TWTCAU`（ETF 分割/反分割）有一筆真實資料方向欄位是空白（00631L,
  115/03/31），任何涵蓋這個日期的請求都會整段隔離失敗；目前靠手動分段
  回補繞過，未來的自動化回補（forward capture、correction check）需要
  知道這個限制。詳見 `docs/decisions/0023-etf-split-result-feeds.md` §4
  與 `docs/step_reports/step-19-e-acceptance-report.md`。
