# ADR-0027：Schema v2——一個 (股票, 來源, 日期) 一列、只新增

狀態：**Accepted**（2026-09-23 owner 決定），ROADMAP Step 35 系列逐步實作。
取代既有表的設計；與 CLAUDE.md、ROADMAP 衝突時以本 ADR 為準。範圍依 ADR-0026。

## 背景

2026-09-23 對全部 61 張表逐張檢討「需不需要、拿掉會損失什麼」。多數冗餘來自
逐步疊加、套用到每個領域的泛用機制：每列一筆的 publication evidence、每列的
重複抓取 observations、業務 hash、seal、來源能力旗標、空的儲存合約。以日行情
為例，每個 key 在 2020–2026 只有一個版本，證據全是 release rule 算出來的時刻，
卻存了約 2,200 萬筆證據列。Step 26-a 的長格式衍生資料更達 56 GB。

## 決策

1. **身分**：股票用官方代號 `stock_id`（一碼到底原則，編碼原則第壹點第二項），
   不再經過 `security` 的代理鍵。指數用「來源 + 官方名稱」。
2. **形狀**：寬表，一個 (股票, 來源, 日期) 一列；業務欄位照官方發布，包含可以
   算出來的淨額與餘額（比照 legacy，owner 決定）。
3. **只新增**：數字改變才新增一列，舊列保留；`recorded_at` 由資料庫產生。
   觸發器禁止 UPDATE / DELETE / TRUNCATE。
4. **出處**：每列有 `fetch_id`；`fetches` 每次抓取一列，帶原始檔的 SHA-256，
   原始檔照舊內容定址存在 `data/raw/`。
5. **公開時間不存**：交易所依固定排程發布的資料，可用時間由 release rule 計算；
   更正後的值以其 `recorded_at` 為可用時間。
6. **拿掉**：每列證據與 observations、業務 hash、`ingest_run_id`、沒有來源的欄位、
   空表、PIT 能力旗標、`import_manifests`／`import_checkpoints`／`import_quarantine`
   （併入 `fetches`）、`raw_artifacts`（併入 `fetches`）、`dataset_*` 設定表與
   `release_rules` 表（改為程式常數）。
7. **型態收緊，但不捨入**：數量與金額 `bigint`。小數欄位是不帶精度的 `numeric`，
   加上 CHECK 限制有效小數位數與整數位數（價格 2 位小數、8 位整數；指數 10 位整數；
   比率 4 位整數），依 2020–2026 實測範圍選定。不用 `numeric(10,2)`：PostgreSQL 先
   依欄位精度轉型、才執行 CHECK，會把官方發布的 30.555 悄悄存成 30.56。有效小數
   位數以 `scale(trim_scale(x))` 計算，所以 30.500000 視為 30.5。
8. **衍生資料**：預設即時計算；量測證明太慢才以寬表實體化（ROADMAP §17）。

## 已定案的表

| 表 | 取代 | 備註 |
|---|---|---|
| `stocks` | `security`、`security_metadata_versions`、`security_tag_versions`、`security_transfer_events` | 今天的 ISIN「股票」清單；原地更新、不刪除 |
| `fetches` | `raw_artifacts`、`raw_artifact_observations`、`ingest_runs`、`import_*` | |
| `trading_days` | `trading_calendar_versions` | 一個交易日一列 |
| `daily_prices` | `daily_price_versions` | 拿掉 `bid_snapshot`、`ask_snapshot` |
| `index_prices` | `market_index*` | 126 個指數：大盤與類股（`stock_data_center.v2.indices`） |
| `valuations` | `official_valuation_versions` | 拿掉只有 TPEx 有的 `dividend_per_share` |
| `institutional_flows` | `institutional_investor_versions` | |
| `institutional_market_flows` | `institutional_market_summary_versions` | |
| `foreign_holdings` | `foreign_holding_versions` | 上櫃只用 MOPS `t13sa150_otc`；拿掉陸資上限、異動原因、申報日期 |
| `margin_trading` | `margin_trading_versions` | 單位：股；拿掉只有 TPEx 有的使用率 |
| `securities_lending` | `securities_lending_versions` | 保留 `adjustment`、兩個限額；拿掉 `note` |

Step 9 的個股 pilot 來源（`twse`、`tpex`）與 `tpex_insti_qfii` 不再保存。

## 尚未討論（仍用 v1 表）

月營收、財報、TDCC、公司行動、衍生資料。月營收與財報會被更正、公開時間逐筆
不同，是否保留某種證據要在各自的討論中決定。

## 遷移

- v2 表與 v1 表並存；`scripts/migrate_to_schema_v2.py` 以受信任的遷移路徑複製
  v1 歷史（`recorded_at` = v1 `ingested_at`），只收今天的普通股與保留的來源。
- 2026-09-23 在 `stockdc_backfill` 執行：六張個股表與 v1 篩選結果以 `EXCEPT`
  雙向比對皆為 0 差異。
- 各領域在 ingestion 改寫到 v2 之後，才 drop 該領域的 v1 表；v1 基礎表在所有
  領域都搬完後才 drop，並重新開始 migration 鏈。
