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
5. **公開時間能算就不存**：交易所依固定排程發布的資料，可用時間由 release rule 計算；
   更正後的值以其 `recorded_at` 為可用時間。公司各自申報、時間逐筆不同的資料
   （月營收、財報）才存 `published_at`，見「35-c 定案」。
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

## 35-c 定案（2026-09-23 owner 決定）

逐個領域以同樣的「需不需要、拿掉會損失什麼」檢討，數字取自 `stockdc_backfill`
（公司行動取自 `stockdc_step19d`）。實作前這些領域仍用 v1 表。

### 可見性

| 表 | 每個 key 的第一列 | 之後的列（更正） |
|---|---|---|
| `monthly_revenues` | `published_at`；NULL 為不可見 | 其 `recorded_at` |
| `financial_reports` | `published_at`；NULL 為不可見 | 其 `recorded_at`（整份財報為一個版本） |
| `shareholding_distributions` | release rule `tdcc_weekly@1`：資料日之後的週日 12:00 | 其 `recorded_at` |
| `corporate_actions` | release rule `corporate_action_ex_date@1`：除權息日 00:00 | 其 `recorded_at` |

以後的每日抓取（`first_capture`）寫入時直接把 `published_at` 設為抓取時刻。

`corporate_action_ex_date@1` 的依據是 owner 決定：當年度的 TWT49U、TWTAUU、`revivt`
檔案都已列出尚未到來的除權息日與算好的參考價（audit §4.10），所以事件在除權息日開盤
前必然已公開。v1 沒有這條規則，公開時間只有回補抓取的時刻，因此歷史事件要到 2026-09
才可見，還原股價在歷史上無法使用。

### 表

**`monthly_revenues`**：key 為 `(stock_id, source, revenue_month)`，`source` 是
`mops_t21sc03_sii`／`mops_t21sc03_otc`，`revenue_month` 是每月 1 日。

- 欄位：`revenue`、`revenue_last_month`、`revenue_last_year_month`、`cumulative_revenue`、
  `cumulative_revenue_last_year`（`bigint` 元；官方千元 ×1,000，實測無小數）；
  `mom_pct`、`yoy_pct`、`cumulative_yoy_pct`（2 位小數、8 位整數）；`note`（照官方，含「-」）；
  `published_at`；`recorded_at`；`fetch_id`。
- 公開時間必須存：公司在 1 日到 10 日各自公告，且每月有 13–311 家國內公司在 10 日之後
  才申報，套用 10 日規則會 look-ahead。v1 的 150,757 個版本中，140,345 個有證明的時間
  （market.csv 公告日、legacy 首次抓取、legacy 檔案證明當時已存在時的法定期限）；
  KY 公司的 8,952 列與更正後的值沒有證明，`published_at` 為 NULL。
- 拿掉：`currency`（全部 TWD）；證據類型（三種在 PIT 上意義相同，來源為 2026M01 以前的
  market.csv 與之後的 legacy 抓取）；業務 hash、observations、`official`／`unknown` 證據。
- 遷移：2026M02 起更正過的 key，legacy 首次抓取的值排在 MOPS 目前的值之前；v1 最後才
  匯入檔案庫，`ingested_at` 的順序是反的。

**`financial_reports`**：一份財報一列；財報內容有任何改變才新增一列（新版本帶完整的事實）。

- 欄位：`id`、`stock_id`、`report_year`、`report_quarter`、`report_category`
  （合併／個體，3,629 份是個體）、`published_at`、`recorded_at`、`fetch_id`。
- 公開時間必須存：4,895 份有 legacy 檔案的首次抓取時刻，早於法定期限。
- 拿掉：`filing_key`、`period_start`／`period_end`（一律 1/1 起）、`currency`、業務 hash、
  seals（財報與事實同一交易寫入，表只新增，已保證完整不變）、observations。

**`financial_report_facts`**：key 為 `(report_id, statement, concept, period_start, period_end)`。

- 欄位：`statement`（同一筆現金同時是資產負債表 1100 與現金流量表 E00210）、
  `account_code`（legacy 的 key）、`concept`（完整 namespace qname，owner 決定不拆）、
  `period_start`（instant 為 NULL）、`period_end`、`unit`、`value`。
- `concept` 與 `account_code` 都留：1,612 組 (報表, 代碼) 中有 108 組在不同年度對應不同 concept。
- 拿掉：4 個 dimension／scenario／segment jsonb、`context_hash`、`entity_identifier`、
  `text_value`、`is_nil`、`decimals`（由單位決定）、`period_type`、`id`。在 16,158,302 筆
  事實中它們全空或可推得。
- 覆寫 CLAUDE.md §33：我們存的三張報表沒有帶 dimension 的事實，adapter 遇到時整份
  quarantine。約 10 GB 降到約 4 GB。
- `quarterly_financial_summary`（EPS 彙總）與 `xbrl_concept_catalog_versions`（空）不留；
  EPS 與 Q4 單季改為衍生資料。

**`shareholding_distributions`**：寬表，key 為 `(stock_id, source, snapshot_date)`，
`source` 目前只有 `tdcc_opendata`。

- 欄位：級距 1–15 的 `holders_N`、`shares_N`、`percent_N`；級距 16 的 `adjustment_shares`、
  `adjustment_percent`（帶正負號，沒有人數）；級距 17 的 `total_holders`、`total_shares`、
  `total_percent`；`recorded_at`；`fetch_id`。
- 百分比 2 位小數、3 位整數（實測 -35.90 到 135.00）。
- 級距定義改為程式常數。1,404 萬列長表變成約 80 萬列，約 2.3 GB 降到約 0.3 GB。
- 拿掉：`tdcc_distribution_schemas`、`tdcc_distribution_schema_buckets`、seals、observations、
  業務 hash、證據（826,795 筆全為規則算出的時刻）。

**`corporate_actions`**：key 為 `(stock_id, source, ex_date)`，15,367 列中唯一。

- 欄位：`event_type`（官方文字：息、權、權息、除息、除權、除權息、退還股款、彌補虧損、
  現金減資、變更股票面額）、`close_before`、`reference_price`、`rights_dividend_value`
  （帶正負號）、`cash_dividend_per_share`、`free_share_ratio`、`rights_ratio`、
  `subscription_price`、`old_shares`、`new_shares`、`cash_return_per_share`、`retracted`、
  `recorded_at`、`fetch_id`、`detail_fetch_id`。
- `detail_fetch_id`（Step 35-c-3，#50 review）：TWSE `TWT49U`／`TWTAUU` 的列表只發布價格，
  股利、配股、認購與減資條件只在每個事件的明細頁，一列因此來自兩個原始檔。`fetch_id` 指向列表，
  `detail_fetch_id` 指向明細；少了它，這兩個 feed 的條件欄追不到原始檔。CHECK 規定恰好這兩個 feed
  有值，其他四個 feed 為 NULL。
- 撤回（列從 feed 消失）新增一列 `retracted = true`（§51.5），2020–2026 發生 0 次。
- 拿掉：
  - 事件表與 `source_event_key`：key 即事件身分，仍是 §51.5 的 `feed + 執行日`。
  - `action_type`、`capital_reduction_kind`：由 (來源, 官方文字) 決定，對照改為程式常數。
  - `announcement_date`、`record_date`、`payment_date`、`earnings_stock_ratio`、
    `capital_surplus_stock_ratio`：15,367 列全為 NULL，result feed 不發布。
  - `source_terms` jsonb（漲跌停價、開盤競價基準等）：留在原始檔。
  - 業務 hash、observations、`corporate_action_retractions`、證據。
- ETF 分割（Step 19-e）不在普通股範圍內。資料重新抓進 v2（每個 feed 一年一個請求）。

**衍生資料：0 張表。**

- `derived_metric_versions`、`derived_computation_runs`：預設即時計算，不存結果。
- `derived_dataset_definitions`：改為程式常數（26-a 的 `DerivationDefinition`）。§42 的
  實作版本改為計算時回傳 git commit，登錄時間改看 git 歷史。
- EPS 彙總、Q4 單季 EPS、還原股價（Step 25）都即時計算。
- 某個指標實測太慢時，才由它自己的 step 以寬表實體化。

## 遷移

- v2 表與 v1 表並存；`scripts/migrate_to_schema_v2.py` 以受信任的遷移路徑複製
  v1 歷史（`recorded_at` = v1 `ingested_at`），只收今天的普通股與保留的來源。
- 2026-09-23 在 `stockdc_backfill` 執行：六張個股表與 v1 篩選結果以 `EXCEPT`
  雙向比對皆為 0 差異。
- v1 表與 v1 程式在所有領域都搬完後，於 35-d 一次刪除，並重新開始 migration 鏈
  （原本每個領域搬完就 drop 的 35-b-2 已併入 35-d）。
