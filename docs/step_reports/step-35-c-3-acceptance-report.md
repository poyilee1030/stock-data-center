# Step 35-c-3 驗收報告

狀態：IN REVIEW (#50)

範圍：公司行動 6 個 result feed 寫入 v2 `corporate_actions` 的新路徑（ADR-0027「35-c 定案」），
以及 2020–2026 在 `stockdc_backfill` 的重新回補。只新增程式；v1 的公司行動 ingestion、表與測試
全部不動，35-d 才一次刪除。衍生資料是 35-c-4。

## 交付

- `src/stock_data_center/v2/corporate_actions.py`：6 個 feed（`twse_twt49u`、`twse_twtauu`、
  `twse_twtb8u`、`tpex_exdailyq`、`tpex_revivt`、`tpex_pvchgrslt`）的 `ingest`、`pending`、`run`
- `src/stock_data_center/db/schema_v2.py`、`migrations/versions/5c1e8d2a7b90_add_corporate_action_detail_fetch.py`：
  `corporate_actions.detail_fetch_id`（#50 review，見下方）
- `src/stock_data_center/v2/exchange_daily.py`：`fetch_and_parse` 回傳的 `log` 可指定寫入的連線
  （list 的 fetch 紀錄與它產生的列同一交易）
- `src/stock_data_center/v2/backfill.py`：`--job corporate_actions/<feed>`，`--job all` 也涵蓋
- `scripts/verify_v2_corporate_actions.py`：v2 與 `stockdc_step19d` 的 v1 逐值雙向比對
- 測試：`tests/integration/test_v2_corporate_actions_ingest.py`（18）

`src/` 改動約 300 行（新模組 273 行，其餘約 30 行），在 CLAUDE.md §1 的 800 行內。

## 寫入規則

| 情況 | 結果 |
|---|---|
| key | (stock, feed, ex_date)：執行過的事件本身（CLAUDE.md §51.5） |
| `executed_through` 之後的列（當年檔案列出的未來事件） | 只計數（`not_yet_executed`），不存 |
| TPEx 三個 feed、TWSE `TWTB8U` | list 的列就有完整條件，不抓明細 |
| TWSE `TWT49U`、`TWTAUU` | 條件在該事件的明細頁；只為今天名單上的股票抓。`fetch_id` 指向列表、`detail_fetch_id` 指向明細 |
| 已存、未撤回的事件，列表的價格與類型和已存的相同 | 不再抓明細；`correction_check` 例外，明細可能自己改 |
| 已存的事件，列表的價格或類型改了 | 重抓明細，新增一列 |
| 明細抓取或解析失敗 | 只擋下那一列；list 的 fetch 記 `succeeded` + `rows_rejected`，該年仍 pending |
| 條件改變 | 新增一列（更正），以 `recorded_at` 為可見時間 |
| 已存事件從 feed 消失（在 executed 範圍內） | 新增一列 `retracted = true`，舊列保留；只寫一次 |
| 撤回後再出現 | 再新增一列 `retracted = false` |
| 明細失敗或被擋下的事件 | 仍算「有列出」，不會被撤回 |
| 數值超出欄位位數 | 該列 `out_of_range` 擋下（35-c-2 的 `check_precision`） |

可見性照 `corporate_action_ex_date@1`（除權息日 00:00 Asia/Taipei），不存 `published_at`。
完整性時點（決定下次回補要不要再抓）：隔年 1 月 1 日 00:00，之前的抓取都會再抓。還沒開始的年份不請求；
「今天」是台北日期。每頁明細在自己的
交易 commit；list 的 fetch 紀錄與它產生的列、撤回列在同一交易，先取該 feed 的 advisory lock。

## 驗收

| 標準 | 結果 | 證據 |
|---|---|---|
| 6 個 result feed 接上 v2 | PASS | `test_the_six_result_feeds_and_their_backfill_keys`；回補 42 個 feed-年全部 `succeeded` |
| `executed_through` 之後的列只計數不存 | PASS | `test_rows_after_executed_through_are_counted_not_stored` |
| 從 feed 消失的列新增 `retracted = true` | PASS | `test_an_event_the_feed_drops_is_retracted_by_a_new_row`、`test_a_retraction_is_written_once`、`test_an_event_whose_detail_fails_is_still_listed_not_retracted` |
| 2020–2026 重新抓進 v2 | PASS | 10,859 列、0 列被擋下、0 次撤回，見下方 |
| 與 `stockdc_step19d` 的 v1 結果比對 | PASS | `verify_v2_corporate_actions.py` 退出碼 0：10,827 列相同、0 列不同、0 列只在 v1 |
| 重跑不重複寫入 | PASS | 重跑 14 秒：每個 feed 跳過 6 年，2026 年 0 列新增、1,579 列不變、0 個明細請求 |
| 每一列追得到它所有數字的原始檔 | PASS | #50 review 後加 `detail_fetch_id`，重抓後 6,199 列全部指向自己的明細頁，見下方 |

每條測試都看過它紅；之後逐一把實作改壞，看測試是否失敗。其中「同一次消失寫兩次撤回」與
「明細失敗的事件被當成消失而撤回」兩種一開始沒有測試抓得到，各補了一條。

## 真實回補（2026-09-24）

```
DATABASE_URL=…/stockdc_backfill .venv/bin/python -m stock_data_center.v2.backfill \
  --job corporate_actions/twse_twt49u … --job corporate_actions/tpex_pvchgrslt \
  --start 2020-01-01 --end 2026-12-31 --purpose gap_fill
```

2026-09-23 16:10 → 19:03 UTC，2 小時 53 分，退出碼 0。list 42 個、明細 6,198 頁，0 次失敗。

| feed | 明細頁 | v2 列 | v1 列（今天的股票） | 相同 | v1 範圍之後 |
|---|---:|---:|---:|---:|---:|
| `twse_twt49u` | 6,052 | 6,052 | 6,041 | 6,041 | 11 |
| `twse_twtauu` | 146 | 146 | 143 | 143 | 3 |
| `twse_twtb8u` | 0 | 9 | 9 | 9 | 0 |
| `tpex_exdailyq` | 0 | 4,532 | 4,518 | 4,518 | 14 |
| `tpex_revivt` | 0 | 107 | 103 | 103 | 4 |
| `tpex_pvchgrslt` | 0 | 13 | 13 | 13 | 0 |
| 合計 | 6,198 | 10,859 | 10,827 | 10,827 | 32 |

- 比較的是每個 key 最新、未撤回的一列，11 個欄位逐值相同（小數正規化後）。
- 「v1 範圍之後」的 32 列除權息日都在 2026-09-14 到 09-23：Step 19-d 的回補只要求到 2026-09-11，
  v1 從沒問過這些日期。
- 每個 key 一列（沒有更正或撤回）；0 列的股票不在 `stocks` 裡；ex_date 範圍 2020-01-02 到 2026-09-23。

全套測試：1312 passed、3 skipped（需 `RUN_LIVE_SOURCE_TESTS`）。

## 踩到的坑

- **原本以為**每個 feed 一年一個請求（ROADMAP 原文）。`TWT49U`、`TWTAUU` 的條件只在每個事件的明細頁，
  實際是 6,198 頁、約 3 小時。因此已存的事件不再抓明細，重跑一年只要一個請求。
- **原本以為**比對腳本的 v1 截止日是它的抓取日 2026-09-16。Step 19-d 要求的是 `--end 2026-09-11`，
  第一次比對因此把 13 列 09-14 到 09-16 的事件算成「v1 範圍內卻沒有」而失敗；改成 09-11 後通過。
- 2454 在 2024 年除息兩次，測試夾具只留 01-04 那次，否則會多一個明細請求。
- `setsid` 在自己是 process group leader 的 shell 裡會 fork 並立刻返回，背景工作的完成通知馬上就來；
  另開一個等 PID 結束的工作才等得到真正的結束——21-a 記過的同一個坑。

## Code review 修正（#50）

- **還沒開始的年份會中斷回補**：`--end` 落在明年，或 UTC 還在前一天時，`executed_through` 早於 1 月 1 日，
  請求直接丟 `ValueError`。改成只處理到今天所在的年份，「今天」用台北日期。
- **被擋下的列會被誤判撤回**：`observation()` 或位數檢查擋下的列沒算「有列出」，已存的事件因此會寫入
  `retracted = true`。改成每一列在讀取前就算有列出。本次回補 0 列被擋，沒有寫入假撤回。
- **明細失敗的事件被算進「不變」**：改為只計略過的與比對後相同的列。
- **v1 比對沒取最新版本**：改成每個事件取 `ingested_at` 最新的一版。`stockdc_step19d` 每個事件只有一版
  （15,367 對 15,367），比對結果不變。
- **（自己找到的）已存事件的列表更正被略過**：TWT49U／TWTAUU 已存的事件整列略過，當年檔案裡前日收盤、
  參考價、權值+息值或類型被改了也不會寫入。改成列表這幾欄和已存的相同才略過明細，不同就重抓明細並新增一列。

修正後以 `--refetch` 重抓全部 42 個列表（97 秒）：10,859 個已存事件全部判定不變、沒有重抓明細；新增 1 列是
今天（2026-09-24）除權息的 1235，舊程式用 UTC 日期還看不到它。比對腳本仍是 10,827 列相同、0 列不同。

- **明細頁的出處斷掉**：TWT49U／TWTAUU 的一列，價格來自列表、股利／配股／認購／減資條件只在明細頁，
  但列只存列表那次的 `fetch_id`，順著它找不到那些條件的原始檔。owner 選擇加欄位：
  - `corporate_actions.detail_fetch_id`，外鍵指向 `fetches`；CHECK 規定恰好這兩個 feed 有值、其他四個 NULL。
    比對「有沒有改變」時不看它，撤回列沿用被撤回那一列的值。
  - migration `5c1e8d2a7b90`：表中已有這兩個 feed 的列就拒絕升級（沒有任何已存的值說得出是哪次明細抓取），
    降級時有列帶著明細連結也拒絕。
  - `stockdc_backfill`：經 owner 同意，暫停 `immutable_corporate_actions` trigger 刪除這兩個 feed 的 6,199 列
    （全是本 branch 寫入的），trigger 恢復後升級，再以 `--refetch` 重抓 2020–2026。
    2026-09-24 03:14 → 06:18 UTC，3 小時 4 分，明細 6,199 頁、0 次失敗，寫回 6,053 + 146 列。

重抓後的檢查：

| 檢查 | 結果 |
|---|---|
| 兩個 feed 的列都有 `detail_fetch_id` | 6,199 / 6,199；其他四個 feed 全為 NULL |
| 指向的是它自己的明細頁 | resource key 的股票代號與 feed 全對；TWT49U 的 locator 日期即 ex_date，6,053 列全對 |
| 明細抓取成功、原始檔存在且 sha256 相符 | 6,199 / 6,199 |
| 沒有兩列共用一次明細抓取 | 0 |
| 明細抓取早於該列的 `recorded_at` | 6,199 / 6,199 |
| 與 v1 比對 | 10,827 列相同、0 列不同、0 列只在 v1；v1 範圍之後 33 列 |
| 一般重跑 | 16 秒，每個 feed 跳過 6 年，0 列新增 |

## 已知限制

- 明細只為今天名單上的股票抓；已下市或不在名單上的股票不存（ADR-0026）。
- 公告日、基準日、發放日與盈餘／資本公積配股的拆分仍無來源（ROADMAP §26.1）。
- `correction_check` 會重抓全部明細，一次全歷史約 3 小時。

## 延後

- 35-c-4：26-a 改接 v2
- 35-d：刪除全部 v1
