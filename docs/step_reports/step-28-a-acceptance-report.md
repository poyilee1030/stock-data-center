# Step 28-a 驗收報告

狀態：IN REVIEW

範圍：前向抓取的第一段——`trading_days` 的 v2 writer、每個 job 的排程宣告，以及不抓取就能
列出待辦的 `plan`；再以 `gap_fill` 把 `stockdc_backfill` 補到今天。每小時的迴圈、通知與
systemd 安裝是 28-b，兩週無人值守與 revision 比率是 28-c（ROADMAP Step 28）。

## 交付

| | 內容 |
|---|---|
| `src/stock_data_center/v2/trading_calendar.py`（新） | `ingest`（一個月一次抓取，只插入新的日子；來源少了已存的日子就 quarantine `calendar_day_removed`）、`pending`、`run` |
| `src/stock_data_center/v2/schedule.py`（新） | `Declaration`、`decide`（純函式）、`DECLARATIONS`（日曆、17 個交易所 job、6 個公司行動、TDCC、4 個月營收、財報）、`plan`、CLI |
| `src/stock_data_center/v2/backfill.py` | `--job trading_days/twse`，在所有 job 之前執行 |
| `src/stock_data_center/v2/financial_reports.py` | `_resource_key` 改為公開的 `resource_key` |
| `src/stock_data_center/ingestion/adapters/trading_calendar.py` | `很抱歉，沒有符合條件的資料!` 讀成 `no_data_for_period`，版本 `twse-fmtqik-trading-calendar:v2` |
| 測試 | `tests/unit/test_v2_schedule.py`（51）、`tests/integration/test_v2_schedule_plan.py`（7）、`tests/integration/test_v2_trading_calendar.py`（7）、`test_pr16_trading_calendar_adapter.py` +1 |
| 文件 | ROADMAP Step 28（以 v2 重讀、拆段、28-a 打勾與裁決）、audit §4.12、README、CLAUDE.md 快照；23-c 報告標為 MERGED |

`src/` +611／−7 行，在 CLAUDE.md §1 的約 800 行門檻內。沒有 schema 變更、沒有 migration。

## 狀態怎麼決定

一個期間的狀態只看它的 `fetches`（財報另加已存的版本）與現在的時刻，`decide` 是純函式：

| 狀態 | 條件 | 分派 | purpose |
|---|---|---|---|
| `not_due` | 還沒到最早可抓時刻 | 否 | |
| `missing` | 沒有完整的抓取 | 是（間隔內否） | 穩定後一天內 `first_capture`，之後 `gap_fill` |
| `given_up` | 缺漏超過放棄期限 | 否，只列出 | |
| `fresh` | 抓過、還沒穩定、不到重讀時間 | 否 | |
| `refresh` | 抓過、還沒穩定、那份已超過 `refresh` | 是 | 同上 |
| `unsettled` | 只在穩定前抓過，現在已穩定 | 是 | 同上 |
| `recheck` | 穩定後抓過，回溯檢查到期 | 是 | `correction_check` |
| `done` | | 否 | |

「完整」是 `succeeded` 且沒有留下列（`close_unverified`）；公司行動要求沒有 reason code；財報的
`financial_industry_issuer`、`outside_v1_universe` quarantine 是最終答案，也算完整。仍缺的期間
第一天每小時問一次、之後每天一次、過了放棄期限不再問。

## 驗收

ROADMAP Step 28 的驗收由三段分擔；28-a 負責下面兩條。

| 驗收 | 結果 | 證據 |
|---|---|---|
| 在任何抓取發生之前，就可以列出待處理的 job 集合 | PASS | `plan(connection, now)` 不收 fetcher；`test_the_plan_lists_every_missing_trading_day_without_fetching` 前後 `fetches` 列數相同；真資料見下 |
| 排程與 gap-fill 的證據不同（28-a 決定 purpose） | PASS | `test_monthly_revenue_inside_its_window_is_a_first_capture`、`test_a_fetch_long_after_the_capture_window_is_a_gap_fill`；月營收與財報的 writer 已在 35-c-2 測過 `first_capture` 存 `published_at`、`gap_fill` 存 NULL |

28-a 的 checklist：

| 項目 | 結果 | 證據 |
|---|---|---|
| 日曆 writer：新增、重跑不重複、少一天時 quarantine | PASS | `test_v2_trading_calendar.py` 7 條 |
| 每條宣告的時刻 | PASS | `test_exchange_daily_times` 等 6 條；每個宣告的 capture 窗口參數化 30 條 |
| `plan` 的每種狀態與 purpose | PASS | `test_v2_schedule.py` 的 `decide` 14 條；`test_v2_schedule_plan.py` 7 條在真的資料列上 |
| 以 `gap_fill` 補到今天，日曆先補 | PASS | 見下：9 個交易日 × 17 個 job 全部成功 |

### 每條測試都紅過

模組還沒寫時整批 import 失敗；寫完後以 mutation 確認每一段邏輯都有測試守住（逐一改壞、跑測試、還原）：

```text
schedule.py
  no give-up              1 failed      no unsettled          2 failed
  no recheck              2 failed      no stale              1 failed
  always first_capture    3 failed      unfinished counted    1 failed
  no backoff              1 failed      no not_due            1 failed
  no final quarantine     1 failed      financial no stocks   1 failed
trading_calendar.py
  no removal check        1 failed      reinsert all          2 failed
  empty as quarantine     1 failed      pending ignores settle 1 failed
```

真資料上跑出兩個測試沒抓到的錯，先補了會紅的測試再修：財報的已存版本多半是 23-c 從檔案庫
寫入的，resource key 是 `financial_filing_archive`，只看 `fetches` 會把 50,622 個股票×季全判成
缺漏（`test_a_stored_financial_version_counts_as_its_fetch`）；而且 2020Q1–2026Q2 中 8,205 個
官方回答沒有申報，每天問它們是每天 13 小時的 MOPS 請求，所以財報只宣告仍開著的季
（`test_financial_reports_follow_the_listed_stocks_and_their_windows`）。

### `stockdc_backfill` 上的 plan（補抓之前）

`python -m stock_data_center.v2.schedule`，2026-09-26T16:37Z（台北 09-27 00:37），2.2 秒，沒有任何抓取：

| 分派 | 數量 | 內容 |
|---|---:|---|
| `missing` `gap_fill` | 193 | 17 個交易所 job 的 09-14、09-15（日曆只到 09-15）；財報 2026Q2 的 161 家 |
| `recheck` `correction_check` | 20 | 09-09～09-11 在 09-16 抓過，穩定後 7 天的回溯檢查到期 |
| `refresh` `first_capture` | 11 | 日曆 2026-09、公司行動 6 個 2026 年、月營收 4 頁的 2026-08 |
| `missing` `first_capture` | 1 | TDCC OpenData：v2 還沒有任何一次線上抓取（歷史來自檔案庫） |

2020-01-02 以來的其餘期間全是 `done`：例如 `daily_prices/twse_mi_index` 1,624 done、3 recheck、2 missing。

### 補抓

日曆先補（`--job trading_days/twse --start 2026-09-01 --end 2026-09-27`）：2026-09 +7 天
（09-16～09-24；09-25 中秋節不在其中），11 天不變。

接著其餘 24 個 job（交易所每日、TAIEX 月檔、公司行動、TDCC；月營收與財報留給 28-b 以
`first_capture` 抓），`--start 2026-09-12 --end 2026-09-27`，purpose `gap_fill`：

| job | 期間 | 成功 | 新增列 |
|---|---:|---:|---:|
| `daily_prices` twse／tpex | 9／9 | 9／9 | 9,448／8,001 |
| `institutional_flows` twse／tpex | 9／9 | 9／9 | 9,343／7,033 |
| `margin_trading` twse／tpex | 9／9 | 9／9 | 9,351／7,218 |
| `securities_lending` twse／tpex | 9／9 | 9／9 | 9,369／7,326 |
| `foreign_holdings` twse／mops | 9／9 | 9／9 | 9,486／8,031 |
| `valuations` twse／tpex | 9／9 | 9／9 | 9,448／7,992 |
| `index_prices` twse／tpex | 9／9 | 9／9 | 657／414 |
| `institutional_market_flows` twse／tpex | 9／9 | 9／9 | 54／72 |
| `index_prices` TAIEX 月檔 | 1 | 1 | 6（12 不變） |
| `shareholding_distributions` TDCC | 1 | 1 | 1,947（週 2026-09-25） |
| 公司行動 6 個 2026 年 | 6 | 6 | 0（1,580 不變） |

沒有失敗、沒有 quarantine、沒有 `close_unverified`。之後 Step 26 的 7 個衍生表以增量重算
（例如 `technical_indicators` 1,945 條序列、17,449 列），API 容器對 2330 的 09-22～09-24 回傳這些列。

補抓後再跑 `plan`（16:43Z）：分派從 225 項降到 185 項——財報 161、回溯檢查 20、月營收 4；
交易所每日的 `missing` 歸零。`overdue` 只剩月營收 4 頁（上次成功抓取在 09-20，超過一天），
那正是 28-b 的迴圈要接手的。

### 全套測試

```text
$ pytest -q
1246 passed, 1 warning in 32.14s
$ ruff check <本 step 動到的檔案>
All checks passed!
```

`ruff check src tests` 全部有 75 個既有問題，都在本 step 沒動的檔案裡。

## 發現（範圍外）

- **KY 公司的財報沒有歷史。** 2026Q2 有 161 家今天的普通股既沒有財報也沒有被問過：118 家 KY、
  38 家金融保險業（依 v1 範圍不收）、5 家其他（2938、3718、7825、7856、8105）。舊系統的 iXBRL
  檔案庫沒有 KY，23-c 從檔案庫匯入；官方 `t164sb01` 對 1590 2026Q2 可以解析（386 個 fact）。
  前向抓取從開著的季收它們；2020Q1–2026Q1 的補抓另外排（ROADMAP 28-a「發現」）。

## 已知限制

- 最早可抓時刻（15:00、融資融券與借券 21:00、財報期限前 35 天）是營運猜測，28-c 以實測取代。
- FMTQIK 在月初第一個交易日收盤前的回答還沒有觀察過（第一次在 2026-10-01）；adapter 依
  `MI_INDEX` 在 09-25 的同一個回答讀成 `no_data_for_period`。
- 休市日以外的預期交易日（`holidaySchedule`）還沒有接上：今天一個平日沒出現在 FMTQIK 裡不會
  通知，要到 28-b。
- `plan` 列出的是狀態；每小時執行它、通知與 systemd 是 28-b。
