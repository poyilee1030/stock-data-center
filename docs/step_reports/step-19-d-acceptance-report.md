# Step 19-d 驗收報告

狀態：IN REVIEW (#28)

範圍：六種公司行動結果資料 2020-01-01 → 2026-09-11 的真實 backfill，以及 ADR-0022
§6–8 為其建立框架的舊系統 `dividend` 對帳報告。

Schema 影響：沒有新的影響（使用 Step 19-c 的 `corporate_action_events`／`_versions`／
`corporate_action_retractions`）。程式影響：`CorporateActionBackfill`（以年為區塊的
backfill，ADR-0022 §6）、`RetryingFetcher`（暫時性 HTTP 狀態的重試，ADR-0022 §7）、
`scripts/reconcile_corporate_actions.py`，以及對共用的
`RawFirstImporter._capture_dependencies` 契約的一項真正的正確性修正（ADR-0022 §8：
明細無法解析的列單獨被 quarantine，而不是整個區間）。

## 真實 backfill 發現了什麼（ADR-0022 §7–8）

兩項真實、實際執行中的發現，在設計寫好之後改變了它：

1. **抓到但無法解析的 dependency 被永遠重播。** TWSE 對一次 `TWT49UDetail` 請求提供了
   HTML 的 `網站維護中` 維護頁；它的原始 bytes 像任何真實回應一樣被建立 checkpoint，
   而每次重試都重新解析同樣的垃圾。已在 `RawFirstImporter._capture_and_parse` 修正：
   內容從來不是真正 JSON 的 `SourceDataError`（`invalid_json`）現在會丟棄它自己的
   checkpoint；真正的 `no_data_for_date`（穩定的領域事實）則保留。
2. **區間層級的 quarantine 讓真實、可取得的資料付出代價。** TWT49U 的 2887 系列特別股
   （台新金的 `2887F`／`2887G`／`2887H`／`2887I`／`2887Z1`，不同年份不同代號）從來沒有
   可用的明細頁——經多次實際請求確認是永久的，不是暫時的故障。TWT49U 的除息日常常
   一次列出幾十支證券，所以為了一個這樣的列讓整天失敗，就是默默丟掉那天其他每一列
   真實、與舊系統相符的資料。已修正：`_capture_dependencies` 現在一次 quarantine
   一列；`_write_business` 仍為每一列註冊事件 identity（所以未解析的列仍是 retraction
   的候選），但只為已解析的列寫入版本。

`scripts/reconcile_corporate_actions.py` 自己的 `duplicate_check` 有程式庫剛在
Step 19-e 為 TWTCAU 修正的同一個 identity bug：它只以 `source_event_key` 分組，把兩支
不同證券共用同一個 locator 日期這種普通情況重複計算。已修正為以
`(security_id, source_event_key)` 分組，也就是真正的 identity（CLAUDE.md §51.5）。

## 驗收證據

對一個幾乎全新的 `stockdc_step19d` 資料庫做真實 backfill，2026-09-16 →
2026-09-17，`--purpose first_capture`。

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| 每種資料的已儲存歷史中，`(feed, code, locator date)` 重複數為零 | PASS | 以 `(security_id, source_event_key)` 分組，六個來源都回傳 0 個重複群組（`twse_twt49u` 7,784 個事件、`twse_twtauu` 149、`twse_twtb8u` 10、`tpex_exdailyq` 7,318、`tpex_revivt` 107、`tpex_pvchgrslt` 13）。 |
| 舊系統 `dividend` 在日期、前日收盤、參考價、權值+息值和類型上都已對帳，每個差異都已分類 | PASS | `reconcile_corporate_actions.py`：`differences: {}`。全部 6,182 列舊系統資料（2020-01-02 → 2026-09-11）都對上一個已儲存的 `twse_twt49u` 列；0 個 `legacy_only`，0 個欄位層級的不一致。 |
| 被 quarantine 的事件（如果有）連同理由一起列出 | PASS | 14 個不同的 locator，全部是 `no_data_for_date`，全部是台新 2887 系列的特別股／認股權子類別：`2887F` 在 2020/2021/2022/2023/2024/2025/2026（每一年）、`2887Z1` 在 2023/2024/2025/2026、`2887G`／`2887H`／`2887I` 在 2026 年新出現。那些日期上的其他每支證券都正常完成。 |

`twse_twtauu`／`twse_twtb8u`／`tpex_exdailyq`／`tpex_revivt`／`tpex_pvchgrslt` 沒有
舊系統基準（Step 19-b：舊系統 `dividend` 只有 TWT49U 形式的列），所以只有重複
identity 的標準適用於它們；五者被拒絕／quarantine 的都是 0，被 retract 的也是 0。

## 驗證

```text
$ .venv/bin/python3 -m pytest tests/ -q
587 passed, 3 skipped, 1 warning in ~107s
$ .venv/bin/python3 -m ruff check <changed files>
# no new findings
$ .venv/bin/python3 scripts/reconcile_corporate_actions.py \
    --database-url postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_step19d \
    --legacy-database-url postgresql+psycopg://user:password@127.0.0.1:5419/stock_db \
    --start 2020-01-02 --end 2026-09-11
# exit 0; differences: {}
```

`tests/integration/test_step19c_corporate_action_ingestion.py` 中新的回歸測試：
`test_a_failing_detail_quarantines_only_its_own_row`、
`test_a_garbled_detail_page_quarantines_its_row_and_clears_its_checkpoint`、
`test_a_genuine_no_data_detail_quarantines_its_row_and_keeps_its_checkpoint`
（取代舊的區間層級 quarantine fixture，它斷言的是現在已被更正的行為）。

對這個 PR 的 `/code-review medium` 在 §8 自己的修正中找到 3 個真正的缺口（ADR-0022
§9），在後續 commit 中修正：一旦帶有被容許列的區間以 `succeeded` 結束，單靠丟棄
checkpoint 永遠不會被觸發（以一次即時的行內重試修正）；`row_quarantined_count` 在每年
自己的 manifest 之上看不到（`CorporateActionBackfillReport` 現在會呈現它）；而
`RetryingFetcher` 實際上沒有重試它自己的註解宣稱涵蓋的同一 URL redirect 迴圈。直接
對照真實的 `stockdc_step19d` 資料驗證：最終資料集中不存在任何列層級的 `invalid_json`
quarantine（只有 §8 存在之前的整個區間 quarantine，已被之後一次成功的嘗試取代），所以
這個 PR 上面自己的驗收數字從未受到這個缺口影響。

第二輪發現單靠行內重試並不足夠：維護時段會比一次即時重試更長，而在第二次收到亂碼
回應時被 quarantine 的列，仍然會永久遺失在一個 `succeeded` 的區間後面，而 backfill
卻回報完成。經過重試仍是亂碼的內容，現在會讓區間以可續跑的方式失敗
（`UnusableSourceResponseError`，ADR-0022 §10）：沒有 quarantine，該年回報 `failed`，
CLI 以 1 結束，而以相同 import id 重跑時，只會重新抓取它從未抓到的明細。

## 已知限制／延後的工作

- TWT49U 的 2887 系列缺口是永久的，只要那個股票家族的成員有除息日，每年都會出現；
  每次發生都不需要進一步處理——它現在會自動單獨被 quarantine。
- 2026 年零星發現的 `2887G`／`2887H`／`2887I`（前幾年沒有）顯示台新此後又發行了更多
  子類別；在其中一個帶著真正的現金股利和可用的明細頁出現之前，不需要做任何事，而那
  會是一個新的、不同的觀察。
- 這個 session 稍早被砍掉的一個 process 留下一個過時的 `running` manifest（2023 年
  原本的整年嘗試，在這個修正存在之前）；無害——它不參照任何目前讀取路徑會用到的
  資料——並且作為準確的紀錄保留，而不是事後去修改它。
