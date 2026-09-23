# Step 35-c-2 驗收報告

狀態：IN REVIEW

範圍：月營收、財報、TDCC 寫入 v2 的新路徑（ADR-0027「35-c 定案」）。只新增程式；
v1 的 ingestion、表與測試全部不動，35-d 才一次刪除。公司行動是 35-c-3，衍生資料是 35-c-4。

## 交付

- `src/stock_data_center/v2/monthly_revenue.py`：4 個 job（上市、上櫃 × 國內頁 `_0`、外國公司頁 `_1`）
- `src/stock_data_center/v2/shareholding.py`：TDCC OpenData 1 個 job，17 個級距轉成一列寬表
- `src/stock_data_center/v2/financial_reports.py`：財報寫入器，一份財報一個版本，連同全部事實同一交易寫入
- `src/stock_data_center/v2/exchange_daily.py`：執行器一般化——key 的日期欄、請求、完成時點可由 job
  指定；`published_at` 由寫入端決定；抓取到解析的共用段抽成 `fetch_and_parse`
- `src/stock_data_center/v2/backfill.py`：`--job all` 涵蓋三個領域
- `scripts/verify_v2_35c_write_path.py`：真實抓取驗收
- 測試：`tests/unit/test_v2_monthly_and_tdcc_jobs.py`（6）、
  `tests/integration/test_v2_monthly_and_tdcc_ingest.py`（11）、
  `tests/integration/test_v2_financial_reports_ingest.py`（13）

`src/` 改動約 555 行（新增 3 個模組 370 行，其餘 185 行），在 CLAUDE.md §1 的 800 行內。

## 寫入規則

| 情況 | 結果 |
|---|---|
| `first_capture` 看到某 key 的第一列 | `published_at` = 抓取時刻 |
| 其他目的（`gap_fill`、`correction_check`）的第一列 | `published_at` = NULL（§32：無法證明何時公開） |
| 之後數字改變的列（更正） | 新增一列，`published_at` = NULL，以其 `recorded_at` 為可見時間 |
| 財報任何一筆事實或報表類別改變 | 新增一個版本，帶完整的事實 |
| 財報 `C` 回答 `檔案不存在!` | 記 `empty`，改問 `A` |
| 兩個 id 都 `檔案不存在!` | `empty`：還沒申報，下次回補再問 |
| 金融業、興櫃等 v1 範圍外 | `quarantined`，視為最終答案，不重抓 |
| 財報事實帶 dimension | 整份 `quarantined`（`dimensioned_fact`，覆寫 CLAUDE.md §33） |

完整性時點（決定下次回補要不要再抓，不影響可見性）：月營收是下下個月 1 日 00:00，財報是法定
期限的隔天 00:00，TDCC 每次都抓（OpenData 只有一個 resource key，永遠是最新一週）。

## 驗收

| 標準 | 結果 | 證據 |
|---|---|---|
| 沿用 35-b-1 的執行器 | PASS | 月營收、TDCC 用同一個 `ingest`；交易所的 70 條既有測試不變 |
| `first_capture` 時把 `published_at` 設為抓取時刻 | PASS | 月營收、財報各有測試；`gap_fill` 為 NULL、更正為 NULL 也各有測試 |
| 財報一份一個版本、同一交易寫入事實 | PASS | 重抓相同文件不新增版本；改一個數字或換類別就新增完整版本 |
| 帶 dimension 的財報事實整份 quarantine | PASS | `test_a_fact_with_a_dimension_quarantines_the_whole_document` |
| 真實抓取數期，與搬過來的資料逐列相同 | PASS | 見下方 |

每條測試都看過它紅；之後逐一把實作改壞（更正沿用抓取時刻、`gap_fill` 也記抓取時刻、忽略完成
時點、TDCC 不抓或被跳過、級距人數漏掉、不改問 `A`、永遠新增版本、允許 dimension、金融業一直
重抓、太早的抓取算完成、忽略類別），每一種都至少讓一條測試失敗。其中「TDCC 被 `pending` 跳過」
與「忽略類別」兩種一開始沒有測試抓得到，各補了一條。

## 真實抓取（2026-09-23）

`scripts/verify_v2_35c_write_path.py --month 2020-01 --month 2023-06 --month 2026-07 --report 1101:2025Q1
--report 2330:2023Q2 --report 1342:2025Q1 --report 6488:2020Q4`：從 `stockdc_backfill`（只讀）複製
這些期間的 v2 資料與 `stocks` 到暫存資料庫，以 `correction_check` 真實抓取，結束後刪除暫存資料庫。
55 秒，退出碼 0。

| 領域 | 抓取 | 既有的 key | 不變 | 不同 | 新股 |
|---|---|---:|---:|---:|---:|
| 月營收 | 3 個月 × 4 頁 = 12 頁 | 5,568 | 5,568 | 0 | 1 |
| TDCC | OpenData 最新一週（2026-09-18） | 1,946 | 1,946 | 0 | 1 |
| 財報 | 4 份（1342 是個體，先 `C` 再 `A`） | 4 份、1,601 筆事實 | 4 份 | 0 | 0 |

- 月營收複製了 5,578 列，是 5,568 個 key：2026-07 有 10 個更正過的 key 各有兩列，每個 key 都和它最新
  那一列相同。
- 「新股」兩列都是 7856 漢測：2026-09-22 才上櫃，v1 從沒登記過它，搬過來的歷史裡沒有它。MOPS 依
  公司目前狀態重新產生頁面（22-b 已證明），所以 2026-07 的頁面有它；TDCC 09-18 那週也有它。腳本把
  「股票在搬移歷史中完全沒有資料」的列另外列在 `stock_not_in_migrated_history`，不算差異。

全套測試：1290 passed、3 skipped（需 `RUN_LIVE_SOURCE_TESTS`）。

## 踩到的坑

- **原本以為**新股不會出現在歷史月份的頁面裡。7856 在 09-22 上櫃，MOPS 的 2026-07 頁面已經有它，
  第一次驗收因此失敗；改成把搬移歷史中完全沒有的股票另外分類。
- **原本以為** TDCC 可以照一般 job 用 `pending` 判斷要不要抓。OpenData 每週都是同一個 resource key，
  抓過一次之後 `pending` 就會永遠跳過它；改成每次回補都抓，並補測試守住。
- 同一個 bind 參數用兩次但型態不同（`VALUES (:s, :s, …)`），psycopg 會報 `AmbiguousParameter`——
  35-b-1 踩過的同一個坑，這次在財報測試的夾具又踩一次。
- 查詢裡的 `LIKE '…%'` 經 `exec_driver_sql` 會被當成佔位符而報錯，同一個指令後面的「重建測試庫」
  就沒執行，全套測試在舊的測試庫上跑出 5 條失敗；改用 `sa.text` 並確實重建後全綠。

## 已知限制

- 從頁面或檔案消失的列（key 有既有列、這次沒有）不處理也不計數，和 35-b-1 相同。
- 財報的回補是「每檔 × 每季」逐一請求，MOPS 每 3 秒一次：全市場一季約 1.6 小時。歷史已由 35-c-1
  搬過來，回補只需補新的一季。

## 延後

- 35-c-3：公司行動的寫入路徑與 2020–2026 回補
- 35-c-4：26-a 改接 v2
