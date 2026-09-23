# Step 35-b-1 驗收報告

狀態：MERGED (#47)

範圍：交易所每日資料寫入 schema v2 的新路徑（ADR-0027）。只新增程式，v1 的
ingestion、表與測試全部不動；刪除 v1 路徑原排在 35-b-2，已併入 35-d。

## 交付

- `src/stock_data_center/v2/exchange_daily.py`：release rule 常數、17 個 job
  （8 個領域、16 個來源；`twse_mi_index` 同時供日行情與指數）、執行器 `ingest`、
  續跑判斷 `pending`、可見性查詢 `visible`
- `src/stock_data_center/v2/backfill.py`：依 `trading_days` 回補，
  `python -m stock_data_center.v2.backfill --job <table/source|all> --start --end`
- `scripts/verify_v2_write_path.py`：真實抓取驗收（見下）
- 測試：`tests/unit/test_v2_exchange_daily.py`（27）、
  `tests/integration/test_v2_exchange_daily_ingest.py`（20）

`src/` 新增 713 行，在 CLAUDE.md §1 的 800 行內。

## 執行器

```text
抓取 → 原始檔先寫入 data/raw → adapter.parse → 只留 stocks 內的股票
     → 與每個 key 最新一列逐欄比對 → 只新增改變的列
```

每次嘗試寫一列 `fetches`，結果對應狀態：

| 情況 | status | reason_code |
|---|---|---|
| 正常 | `succeeded` | 無；部分列被拒為 `rows_rejected` |
| 該日無資料 | `empty`，不算完成，下次再抓 | `no_data_for_date` |
| 無法對應合約（表頭、單位、數值） | `quarantined`，原始檔保留 | adapter 的原因碼 |
| TAIEX 收盤與 `MI_INDEX` 不同 | `quarantined` | `close_mismatch` |
| TAIEX 某日 `MI_INDEX` 尚無收盤 | `succeeded`，該日不寫入，該月不算完成 | `close_unverified` |
| 連線失敗 | `failed`，無原始檔 | `fetch_error` |
| 不是來源回應（維護頁） | `failed`，原始檔保留，立即重抓一次 | `invalid_json` |

fetch 列與它產生的值列在同一個交易內提交，所以 `succeeded` 一定有它的列。

寫入時不做 PIT 判斷：抓到什麼就存什麼，client 能看到什麼由 `visible` 在讀取時決定
（owner 裁定）。

## 驗收

| 標準 | 結果 | 證據 |
|---|---|---|
| 真實抓取數個交易日，寫入結果與既有 v2 資料逐列相同 | PASS | 見下方「真實抓取」：31,148 列 0 列不同 |
| 同一天重跑不新增列 | PASS | `test_the_same_day_again_adds_no_row_but_logs_the_fetch`；真實抓取即為重跑 |
| 解析失敗保留原始檔並記 `quarantined` | PASS | `test_a_file_that_does_not_parse_is_kept_and_quarantined`、`test_the_raw_file_is_on_disk_before_anything_parses_it` |
| 範圍外股票不寫入 | PASS | `test_only_stocks_in_the_universe_are_written`（MI_INDEX 一份檔 1,000 多檔，只寫入 2 檔） |
| 可見性查詢：原始值 | PASS | 規則時刻前 1 秒不可見、規則時刻可見，即使 `recorded_at` 晚得多；結算前記錄的暫時值在規則時刻由結算值取代 |
| 可見性查詢：更正值 | PASS | `recorded_at` 前 1 微秒仍是原始值，`recorded_at` 起為更正值 |
| release rule 為程式常數 | PASS | `RELEASE_RULE = exchange_daily_settled@1`；Python 與 SQL 兩種算法逐日相同 |

每條測試都看過它紅：先寫測試再寫實作；之後逐一把實作改壞（拿掉範圍過濾、比對、
兩種可見性、TAIEX 檢查、重抓、續跑、`empty`、raw-first、整數檢查），每一種都至少
讓一條測試失敗。raw-first 那一種原本沒有測試抓得到（`record_fetch` 會再存一次），
所以補了「parser 當掉時原始檔已在磁碟上」這條。

## 真實抓取（2026-09-23，review 修正後重跑）

`scripts/verify_v2_write_path.py --date 2020-01-02 --date 2023-06-15 --date 2026-09-11`：
從 `stockdc_backfill`（只讀）把這三天的 v2 資料、`stocks`、TAIEX 月份的清單收盤與
引用到的 fetch 複製到暫存資料庫，再以 `--refetch` 對 17 個 job 真實抓取，結束後刪除
暫存資料庫。78 秒，退出碼 0。

| 表 | 既有列 | 真實抓取後不變 | 新增 |
|---|---:|---:|---:|
| `daily_prices` | 5,329 | 5,329 | 0 |
| `valuations` | 5,328 | 5,328 | 0 |
| `institutional_flows` | 4,736 | 4,736 | 0 |
| `institutional_market_flows` | 42 | 42 | 0 |
| `foreign_holdings` | 5,329 | 5,329 | 0 |
| `margin_trading` | 4,973 | 4,973 | 0 |
| `securities_lending` | 5,033 | 5,033 | 0 |
| `index_prices` | 378 | 375 | 0 |
| 合計 | 31,148 | 31,145 | 0 |

0 列不同。除了下面 3 列，每個 (表, 來源, 日期) 的既有列數都等於該次抓取的「不變」列數，
所以既有的每一列都在官方檔案裡原樣出現。

TAIEX 的請求單位是整個月，2026-09 月檔有 8 天被列為 `unverified`、沒有寫入：
- 09-14、15、16：`stockdc_backfill` 有 TAIEX 列，但 `MI_INDEX` 只回補到 09-11，沒有清單收盤可比。這就是上表少的 3 列。
- 09-17、18、21、22、23：遷移歷史之後的日子。修正前這 5 天被當成新增列寫入；現在要等 `MI_INDEX` 有收盤才寫。

`stockdc_backfill` 全歷史中，兩邊都有收盤的 1,627 天 0 天不同。

## 續跑與遷移歷史

`fetches.dataset` 與 resource key 都沿用 v1，所以遷移過來的 77,332 列抓取紀錄直接
決定續跑。在 `stockdc_backfill` 以 2020-01-02 到 2026-09-11 計算待抓期間：16 個每日
job 各 1,627 個交易日，待抓 0；TAIEX 81 個月，待抓 1（2026-09，尚未結算）。review 修正
後重算，結果相同。

## 踩到的坑

- **原本以為**「最新一次抓取成功」就代表該期間完成。實際上 TAIEX 的月檔包含今天，
  當月抓過一次就會被視為完成、之後的日子再也不抓；D+1 03:00 前抓的日檔也還可能變。
  修正後：一個期間要「最後一次抓取成功或為空，且抓取時刻晚於期間最後一天的規則
  時刻」才算完成。三條整合測試守住。
- **原本以為**結算前抓到的列是 PIT 問題、不該寫入，一度在寫入端過濾。owner 裁定：
  PIT 管的是 client 詢問時給什麼，不是寫入；寫入照單全收，已撤回過濾。
- **原本以為**把 raw-first 的 `store.put` 拿掉會有測試失敗；其實 `record_fetch` 會再存
  一次，只有「parse 當掉」時才看得出順序。已補測試。
- `pgrep -f <腳本名>` 會比對到自己所在的 shell 指令列；確認殘留要用 `ps -p <pid>`。
- **#47 code review 抓到的三件事**：
  - **原本以為** `MI_INDEX` 還沒有收盤時放行 TAIEX、事後再對帳就夠了。但 `sorted(JOBS)` 讓 TAIEX 先跑，全新回補時每一天都放行，而且對帳腳本讀的是 35-b-2 要刪的 v1 表。改為沒收盤的日期不寫入，等下次再比。
  - **原本以為**空回應就是「那天沒資料」。但期間來自 `trading_days`，交易日的空回應其實是缺口。
  - **原本以為**每個 key 的第一筆就是結算值。結算前抓到的列只是暫時值，規則時刻起應改用結算後的第一筆。
- `recorded_at` 由資料庫蓋章，測試無法用假的抓取時刻重現「結算前記錄」；可見性測試直接插入帶明確 `recorded_at` 的列，做法和遷移相同。
- 本機測試庫 `stockdc` 殘留舊列時，`test_phase4_monthly_revenue` 會失敗（切回原始 commit 也一樣）。重建測試庫後全綠。

## 已知限制

- 從 feed 中消失的列（key 有既有列、這次檔案沒有）目前不處理也不計數；交易所每日
  資料在 2020–2026 沒有發生過（上表既有列數等於不變列數）。
- `MI_INDEX` 對某個交易日一直沒有 TAIEX 收盤（例如那份檔被 quarantine）時，該月
  TAIEX 會一直待抓；這是刻意的 fail-closed，會以 `close_unverified` 顯示。
- 兩個 `twse_mi_index` job 各自抓一次同一個 URL（v1 也是）；原始檔內容定址，不會重複存。

## 延後

- 刪除 8 個領域的 v1 writer、服務、CLI、測試與 v1 表：原為 35-b-2，已 SUPERSEDED、
  併入 35-d（在 `stockdc_backfill` 執行前須 owner 確認）。
- Step 28 排程：每日抓取直接用 `python -m stock_data_center.v2.backfill`。
