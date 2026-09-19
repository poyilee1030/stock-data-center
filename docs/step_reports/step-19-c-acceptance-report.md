# Step 19-c 驗收報告

狀態：IN REVIEW (#26)

範圍：公司行動匯入路徑——一個區間檔案的集合式事件註冊與版本寫入、TWSE 區間檔案
需要的逐列明細抓取、對在檔案已執行涵蓋範圍內消失的列所屬事件做 retraction、六個
source policy 及其證據類型，以及 CLI。

Schema 影響：migration `02f0a144b1fc` 新增 `corporate_action_retractions`（只可附加、
不可變），並為六個結果資料來源宣告 `dataset_sources` 列
（`accepted_evidence_types = ['official', 'capture_bound']`，沒有 release rule——
ADR-0022 §4）。Migration 影響：upgrade／downgrade 都通過；只要有任何
`ingest_runs`／`import_manifests`／retraction 列參照宣告的來源，downgrade 就拒絕。
PIT 影響：版本照常從它們自己的 publication evidence 繼承可見性；retraction 目前是只
在 System PIT 下的事實（ADR-0022 §1）。

`src/` 改動 +622/−1 行（`corporate_action.py` 329、`lifecycle.py` +109、
`market_reference/ingestion.py` +100、`cli.py` +56、`metadata.py` +22、其他六個
importer 因為一個共用的掛鉤參數各 +1、`policy.py` +1）。

## 設計決策（ADR-0022）

共用的 `RawFirstImporter` 框架以前從不需要的兩件事：

- **Retraction** 是自己的只可附加的 `corporate_action_retractions` 表，而不是
  `corporate_action_versions` 的 revision——那張表的列以 `action_type` 分型，並要求
  每種類型需要的條件，而 retraction 一個都沒有。以 `(event_id, raw_artifact_id)`
  去重；之後重新出現是否撤銷 retraction，延到有讀者需要答案時再決定，因為目前還沒有
  讀者。
- **逐列的明細抓取發生在寫入 transaction 開啟之前。** `RawFirstImporter` 新增了
  `_capture_dependencies`（預設：無），在主 resource 解析之後、`_write_business` 之前
  呼叫，具有與 `parse()` 相同的 quarantine／可續跑處理。`CorporateActionImporter` 用它
  抓取 TWT49UDetail／TWTAVUDetail 頁面，並為每一列建立完成的
  `CorporateActionObservation`，所以 `_write_business` 是純粹的寫入，而來自清單或明細
  頁的 `SourceDataError` 都以同樣的方式把整個區間送進 quarantine。

## 驗收證據

全部來自 `tests/integration/test_step19c_corporate_action_ingestion.py`，使用 19-b 在
2026-09-16 抓到的同一批 TWT49U／明細 fixture（2454 息、6442 權、2543 權息）。

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| 更正回歸測試：同一個 locator、條件改變 → 同一事件的新 revision | PASS | `test_a_corrected_detail_creates_a_new_revision_of_the_same_event`：更正後的現金金額建立 1 個新版本；該事件在 `TWT49U:20240104` 底下仍恰好有 2 個版本。 |
| 被移除的列產生 retraction，而不是刪除 | PASS | `test_a_row_dropped_from_the_covered_range_is_retracted_not_deleted`：在同一個一月時間窗的重跑中 6442 消失；它的事件／版本列和 hash 前後逐 byte 相同，並出現一筆 `reason = 'absent_from_covered_range'` 的 `corporate_action_retractions` 列。 |
| 在已執行涵蓋範圍之外的列，絕不會因為缺席而被 retract | PASS | `test_a_row_outside_the_covered_window_is_never_retracted`：2543（五月）在只含一月的重跑中缺席；寫入的 retraction 列為零。 |
| 重跑一個區間不產生 revision | PASS | `test_rerunning_the_same_range_creates_no_revision`：新建 0、去重 3；retraction 0（相同的 bytes 證明不了任何缺席）。 |
| 被 quarantine 的區間保留其 raw artifact，不寫入任何業務列 | PASS | `test_a_failing_detail_quarantines_the_range_and_keeps_raw_artifacts`：回應 `無相關資料` 的明細頁拋出 `ResourceQuarantinedError("no_data_for_date")`；0 個事件、0 個版本，保留 ≥2 個 raw artifact（清單 + 失敗的明細）。 |
| 集合式註冊 + 明細補完端到端運作 | PASS | `test_a_range_registers_events_and_completes_them_from_their_details`：3 列 → 3 個事件、3 個版本，三筆都是 `capture_bound` 證據列；權息列儲存的 `cash_dividend_per_share`／`free_share_ratio`／`rights_ratio`／`subscription_price` 與來源明細頁完全相符（`0.4`、`0.14`、`0.20211906001`、`33`）。 |

## 驗證

```text
561 passed, 3 skipped in 101s
```

3 個 skip 是既有的 `RUN_LIVE_SOURCE_TESTS=1` 官方端點 pilot，與這個 step 無關。為
這個 step 寫的測試都沒有使用實際的網路呼叫；六個新的 integration test 都使用 19-b 已
經抓到的 fixture。

- `alembic upgrade head`／`downgrade -1`／`upgrade head`／`alembic check`：乾淨，
  沒有 drift。
- 對這個 step 碰到的每個檔案執行 `ruff check`：零個新發現。`lifecycle.py` 和
  `trading_calendar.py`／`daily_market.py` 中兩項既有的 import 未排序發現，早於這個
  branch（已對照 `main` 確認），與 19-b 對 `adapters/__init__.py` `__all__` 排序的
  先例一致。
- `docs/data_domain_inventory.json`：`corporate_action_retractions` 分類為排除的
  （只有 provenance 的）表；儲存契約涵蓋測試通過。

### 這個 step 的 migration 需要調整一個既有測試

`tests/integration/test_step19a_corporate_action_terms.py` 在三個測試中從 head
downgrade 到 `8e4b2c7d9a13` 之前的 revision，以單獨演練那個 migration 自己的比率可
表示性拒絕。一旦這個 step 的 migration 疊在上面並宣告這些來源，那些測試的 `write()`
helper 建立的 `ingest_runs`（`tpex_exdailyq` 上真正的公司行動資料，是六個宣告來源
之一）就被一個真正的 foreign key，從 `ingest_runs` 參照到
`dataset_sources(dataset_code, source)`——所以在那些資料存在時，試圖穿過這個 step 的
migration 的 downgrade 不可能成功，這是刻意的設計（只可附加的 source policy 保護，
與 `7a2c9e4d1b58` 自己的防護及其說明的理由相同）。這三個測試現在明確地把 schema 固定
在 `8e4b2c7d9a13`（`AT_STEP_19A`），而不是跟著 `"head"` 走，所以它們測的正是它們一直
在測的東西，與之後疊上去的任何 migration 脫鉤。ADR-0022 §5 為這個 step 自己的
downgrade 防護記錄了同樣的理由。

## 已確認的範圍排除

- 沒有 backfill，也沒有抓取 2020-2026 的每一個明細頁；那是 19-d 的工作
  （2020-01-01 → 2026-09-11，約 7,800 次 `TWT49UDetail` 請求）。
- 沒有舊系統 `dividend` 對帳；那是 19-d 的驗收標準。
- 沒有公司行動的 resolver／讀取服務，不論是否考慮 PIT——還沒有任何東西使用這些
  資料，而 ADR-0022 §1 把撤銷 retraction 的問題延到新增第一個讀者的 step。
- 沒有 ETF 分割／反分割資料（19-e）。

## 已知限制／延後的工作

- retraction 的可見性目前還沒有考慮之後重新出現會撤銷 retraction 的情況——刻意延後
  （ADR-0022 §1），等到有使用者需要答案時再決定，而不是現在去猜。
