# Step 17-b 驗收報告

狀態：IN REVIEW

範圍：全市場每日價格匯入路徑

Schema 影響：無。`daily_price_versions` 已涵蓋每個有來源的欄位。
Migration `5e3b8d1a9c42` 只新增列：兩個 `dataset_sources` 及其可接受的證據類型、
它們的 release rule 對應，以及兩個 `daily_price` 預期涵蓋宣告。
PIT 影響：沒有新的影響——兩個來源都遵循 `exchange_daily_settled@1`，也就是
Step 15-c 已經套用到 `daily_price` 的規則。
`src/` 改動 +633/−10 行。

## 基準

Step 17-a 的解析（以 #19 合併）及其對帳：五個市場日共 4,423 列舊系統資料，差異
為零。這個 step 儲存的是同樣的列，所以同樣的比較改為對照**已儲存**的版本重跑，
而不是解析出的版本——否則一個正確的解析加上一個會遺失東西的寫入，看起來會一模
一樣。

在這個 step 之前，`daily_price` 有兩個來源（`twse`、`tpex`），沒有宣告任何預期
涵蓋；`dataset_expected_coverage` 只有 `trading_calendar`。

## 驗收證據

透過 CLI 實際匯入從零 migrate 的資料庫，每次一個請求，purpose 為 `gap_fill`：

```text
twse_mi_index   2026-09-11  1,379 rows  1,379 created  1,379 evidence  variant allbut0999
tpex_otc_quotes 2026-09-11  1,012 rows  1,012 created  1,012 evidence  variant volume_in_lots
twse_mi_index   2020-01-02  1,114 rows  1,114 created  1,114 evidence  variant allbut0999
tpex_otc_quotes 2020-01-02    876 rows    876 created    876 evidence  variant prices_only
tpex_otc_quotes 2020-04-30    885 rows    885 created    885 evidence  variant volume_in_thousand_shares
total                       5,266 versions, 5 raw artifacts, one request each
```

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| 每個市場一個真實交易日以 raw-first 方式端到端匯入 | PASS | 上面五個市場日，涵蓋全部三種 TPEx header 版本，每個都實際抓取，並透過 Step 9 raw-first 生命週期儲存：artifact 在解析之前被抓取並建立 checkpoint，業務列與 checkpoint 完成在同一個 transaction 中寫入。 |
| 儲存的值與舊系統 `daily_quotes` 相同 | PASS | 同樣的 4,423 列舊系統資料，對照的是**資料庫中**的內容，而不是 adapter 回傳的內容：開高低收、成交量、成交金額和成交筆數差異為零，也沒有只在舊系統存在的列。 |
| 單位在寫入後仍然正確 | PASS | 2330 在 2026-09-11 儲存 `last_bid_volume = 1078`，而 manifest 記錄 TWSE 的 `source_units.disclosed_volume = share`，對照 TPEx 的 `lot_1000_shares`。這是 #19 的 review 發現，在管線的最末端檢查，而不只是在解析時。 |
| 重跑同一天不產生 revision | PASS | 第二次匯入 2026-09-11：`created 0, deduplicated 1379`，證據 `created 0, deduplicated 1379`，多一筆 `raw_artifact_observations` 列。重複的抓取仍可稽核，而不會捏造 revision。 |
| 內容改變時產生 revision | PASS | 在 payload 中更正一個收盤價，恰好產生一個新版本；其餘 1,378 個去重。 |
| 不會與 Step 9 pilot 來回產生 revision | PASS | 不同的 source code。一個回歸測試斷言匯入的列只帶有全市場來源，而且兩段歷史從不共用同一個 `(security, trade_date)` 列。 |
| 來源沒有資料的日期被 quarantine | PASS | 2024-07-24 拋出 `no_data_for_date`；raw artifact 被保留，不寫入任何業務列，`import_quarantine.reason_code` 帶有這個 code——17-c 就是靠它區分無害的跳過與真正的失敗，而不必重新推導日曆。 |
| 匯入的列在 Market PIT 下解析 | PASS | 2330 在 2026-09-11 的資料以 `exchange_daily_settled@1` 在 2026-09-11T19:00Z 解析——也就是 Asia/Taipei 09-12 的 03:00。 |
| 證據依循宣告的 purpose | PASS | 全部 5,266 個版本都只帶 `release_rule`：`gap_fill` 無法證明首次看到。`first_capture` 執行則會為每個建立的版本宣稱一個 `capture_bound`。 |
| 已宣告預期涵蓋 | PASS | `daily_price/TWSE → twse_mi_index` 和 `daily_price/TPEx → tpex_otc_quotes`，頻率 `trading_day`，時間窗從 2020-01-02 起，兩者都宣告使用 Step 16 量測過的 TWSE 日曆。 |
| downgrade 拒絕讓歷史成為孤兒 | PASS | 有匯入的列時，`downgrade 4d9f2a6c8b17` 在修改任何東西之前拋出 `P0001`。以移除防護並看著回歸測試失敗，確認過它會失敗。 |

與舊系統 `stock_db` 的對帳，取自已儲存的列：

```text
2026-09-11 twse_mi_index    stored=1379 legacy=1092 diffs={}
2026-09-11 tpex_otc_quotes  stored=1012 legacy= 862 diffs={}
2020-01-02 twse_mi_index    stored=1114 legacy= 955 diffs={}
2020-01-02 tpex_otc_quotes  stored= 876 legacy= 752 diffs={}
2020-04-30 tpex_otc_quotes  stored= 885 legacy= 762 diffs={}
total legacy rows compared: 4,423
```

## 設計決策

**集合式寫入，逐列規則。** 一個全市場檔案約 1,300 支證券，既有的逐列路徑每次請求
會發出約 4,000 個陳述——足以讓 17-c 約 3,300 次請求變得不可行。註冊、版本寫入、
證據規劃和證據寫入現在各只需要一兩個陳述。重要的部分**沒有**改變：資料庫仍然產生
`business_content_hash` 和 `ingested_at`，未改變的觀察仍然重用既有的版本，而對應回
那個版本是依據資料庫做 hash 的業務值，絕不是在 Python 中重新計算的 hash。一個交易日
端到端匯入約 1.9 秒。

**`ON CONFLICT DO NOTHING ... RETURNING`，而不是 `DO UPDATE`。** 常見的 `xmax = 0`
upsert 技巧可以在一個陳述中區分新建和既有，但 `immutable_daily_price` 是
`BEFORE UPDATE OR DELETE` trigger：一個什麼都不改的 `DO UPDATE` 也會觸發它並被拒絕。
因此 insert 只回傳它建立的列，其餘的讀回並依業務值對應。

**`plan_many` 只解析一次規則。** 對一個 `(dataset_code, source)` 來說，release rule
和可接受類型的允許清單是常數，而規則時刻對 1,300 列共用的交易日也是常數。每個版本
已經證明的抓取以一次分組查詢讀取，而不是每列一次。決策本身仍是 `evidence_plan`，
沒有改變，並與逐列路徑共用——批次處理只是往返次數不同，不是第二套政策。

**importer 宣告自己的市場。** `_source_semantics` 記錄市場、兩種交易單位和揭露量的
單位，所以只要其中任何一個改變，設定 fingerprint 就會改變，語意改變的續跑匯入會被
拒絕，而不是默默混在一起。

## 這對既有測試的代價

Step 16 的涵蓋測試自己宣告了 `daily_price/TWSE` 的涵蓋，使用 pilot 來源 `twse`，
並附上一段註解寫著 *「Step 17 will ship this declaration with its adapter; here it
is a fixture.」*。現在確實出貨了，所以那些測試改指向真正的 source code。migration
一加入就有五個失敗，這正是宣告在發揮作用：fixture 和出貨的宣告不一致，而出貨的那個
勝出。

## 驗證

從零 migrate 到 `5e3b8d1a9c42` 的資料庫：

```text
421 passed, 3 skipped, 1 warning
```

本 step 之前的基準：409（Step 17-a）。差異就是這裡新增的 12 個 integration test——
11 個在 importer 存在之前寫好，另一個來自下方的 review。

在乾淨資料庫上的 migration 往返：`upgrade head` 寫入六列初始資料——兩個
`dataset_sources`、兩個 `dataset_release_rules`、兩個 `dataset_expected_coverage`——
`downgrade 4d9f2a6c8b17` 恰好移除這些，兩個 pilot 來源的允許清單不動，`upgrade head`
重新寫入。有匯入的歷史時，downgrade 會先拒絕。

`ruff check` 對每個碰到的檔案，相對於 `main` 都沒有回報新問題。

## Code review 發現

四項發現，在改動任何東西之前都經過驗證；沒有一項是誤報。

| # | 發現 | 驗證方式 | 處置 |
| --- | --- | --- | --- |
| 1 | 多列觀察 insert 每列綁定約 21 個參數，所以約 3,100 列時會超過 PostgreSQL 每個陳述 65,535 個參數的上限 | 編譯實際的陳述：每列恰好 21 個參數，所以上限是 3,120。一個寫入 4,000 筆觀察的回歸測試以 `number of parameters must be between 0 and 65535` 失敗。 | **已修正。** 每個多列 insert 現在都依它實際綁定的參數分批，從列本身計算，所以新增欄位不會默默移動這道懸崖。證據 insert 也是同樣的形狀，每列 12 個參數——上限 5,461，而 `first_capture` 執行在約 2,730 支證券時就會碰到，因為它為每個版本規劃兩筆證據。 |
| 2 | manifest 缺少其他每個 importer 都有的 `publication_time` key | 閱讀：三個 importer 輸出它，沒有任何東西讀取它——而對 `daily_price` 來說，常數 `"unknown"` 從 Step 15-c 起就是**錯的**。 | 已修正，但不是照抄那個常數。manifest 現在回報實際決定可取得時間的規則（`exchange_daily_settled@1`），以及這次執行寫入的證據類型。Step 9 pilot importer 中同樣過時的 `"unknown"` 也一併更正：同一個資料集、同一條規則，而 manifest 是稽核紀錄。 |
| 3 | 涵蓋 insert 使用 `ON CONFLICT DO NOTHING`，而它的兄弟刻意使用 `DO UPDATE` 來修復既有的列 | 閱讀。`main` 上不存在這樣的列，但一個仍指向 Step 9 pilot 來源的宣告，會讓涵蓋報告讀錯歷史——而且是默默地。 | 已修正。upsert 會修復該列。這個 migration 是 `daily_price` 涵蓋意義的權威。 |
| 4 | migration 的 docstring 把改動歸到 Step 17-a，而那一步把儲存和證據列為範圍外；驗收報告說四列，實際寫入六列 | 計數：2 + 2 + 2。 | 已修正。兩者都是在 Step 17 還是單一 step 時寫的。 |

真正重要的是第 1 項。它是潛在的而不是正在發生的——目前最大的市場日是 1,379 列——
但上市範圍越過那條線時，它會讓匯入直接失敗，而 17-c 正是會踩進去的那個 step。

## 已確認的範圍排除

- 沒有 backfill：17-c 負責日期區間 runner、2020-01-02 → 2026-09-11 的執行、提交進
  repo 的對帳工具，以及無 metadata 報告。這裡的 CLI 每次呼叫匯入一個交易日。
- TWSE artifact 的指數區段沒有動；Step 18 重用同樣的 raw artifact。
- Step 9 的個股 pilot 不變，仍可用於抽查。
- 沒有還原價格，也不宣稱可用於報酬或指標：§51.4 的關卡仍在等待公司行動歷史。
