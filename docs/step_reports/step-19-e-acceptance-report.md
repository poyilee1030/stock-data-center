# Step 19-e 驗收報告

狀態：IN REVIEW (#27)

範圍：以實際抓取驗證 TWSE `TWTCAU` 和 TPEx `etfSplitRslt`／`etfRvsRslt`（在 19-a
發現，從未在 Step 19 原本的六種資料契約中），新增它們的 adapter 和資料集來源，並對
三者執行真實的 2020-01-01 → 2026-09-11 backfill。

Schema 影響：migration `9f3d7c2e5a41` 再宣告三個 `dataset_sources` 列
（`twse_twtcau`、`tpex_etfsplitrslt`、`tpex_etfrvsrslt`），形狀與 `02f0a144b1fc`
相同——`capture_bound` 證據，沒有 release rule。沒有新表。Migration 影響：在
`stockdc` 上 upgrade／downgrade 都通過；只要有任何 `ingest_runs`／`import_manifests`
列參照宣告的來源，downgrade 防護就拒絕。

`src/` 改動 +141/−6，分布在 4 個檔案（`corporate_action.py` +125：
`TWSEETFSplitAdapter`、`TPExETFSplitAdapter`、`TPExETFReverseSplitAdapter`，以及
`_TWSEListAdapter` 上的 `_build_locator` 掛鉤；`adapters/__init__.py` +6 個 export；
`cli.py` +11，用於三種資料的選項；`models.py` +5，把三種資料加進 `RESULT_FEEDS`，
沒有它的話 `ExchangeLocator` 會以 `unknown_feed` 拒絕每一列）。

## 設計決策（ADR-0023）

實際驗證（`docs/source_field_audit.md`）在寫任何程式之前發現了兩件決定設計的事：

- **`TWTCAU` 沒有發布換股比率，也沒有明細頁**——只有一個 `分割(反分割)` 方向標籤，
  以及其他每種資料都有的那兩個官方價格。`TWSEETFSplitAdapter` 儲存
  `action_type="other"`（TWTB8U 既有的無比率先例），而不是從兩個價格相除推導出
  `old_shares`／`new_shares`。
- **TPEx 的兩種資料在整個 2020-2026 期間從未列出任何一列。** 它們的 `_row()` 對任何
  列都拋出 `unverified_schema`，而不是重用從未針對這種資料驗證過的 `pvChgRslt` 明細
  標籤 schema。

## 驗收證據

對 `stockdc_step19e`（全新資料庫，migrate 到 `9f3d7c2e5a41`）做真實的實際
backfill，2026-09-17，`--purpose first_capture`。

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| 欄位、單位和歷史以實際抓取驗證，而不是假設 | PASS | `docs/source_field_audit.md` 的 ETF 分割章節：`TWTCAU` 實際抽樣，2020-01-01 → 2026-09-11 共 11 列真實資料；兩種 TPEx 資料實際抽樣，`totalCount: 0`。 |
| 三種資料都有 2020-01-01 → 2026-09-11 的真實 backfill | PASS | `twse_twtcau`：7 次逐年匯入，2020–2025 整年成功；2026 在有歧義的列處拆成 `2026-01-01→2026-03-31`（單獨被 quarantine）和 `2026-04-01→2026-09-11`（成功，2 個事件）。共儲存 10 個事件。`tpex_etfsplitrslt`／`tpex_etfrvsrslt`：各一次整段區間匯入，成功，0 個事件（真的沒有，而不是「還沒抓」）。 |
| 每種資料的已儲存歷史中，`(feed, code, locator date)` 重複數為零 | PASS | `SELECT security_id, source_event_key ... GROUP BY ... HAVING count(*)>1` 對三個來源都回傳 0 列。`TWTCAU:20251022` 由兩支不同的 ETF（00673R、00706L）共用，在真正的 identity `(security_id, source, source_event_key)` 下不是重複。 |
| 無法判斷方向／類型的列附上理由被 quarantine，而不是猜測 | PASS | 00631L（115/03/31）發布了空白的 `分割(反分割)` 儲存格；包含它的兩個區間（`2020-01-01→2026-09-11` 和 `2026-01-01→2026-03-31`）都以 `reason_code = unknown_event_type` 被 quarantine，raw artifact 保留，兩者都沒有寫入任何業務列。 |

沒有舊系統對帳標準：舊系統 `stock_db` 從未收集 ETF 分割或反分割（CLAUDE.md §78 只
適用於有舊系統基準的地方）。

## 驗證

```text
$ .venv/bin/python3 -m pytest tests/ -q
572 passed, 3 skipped, 1 warning in 104.03s
$ .venv/bin/python3 -m alembic upgrade head && alembic downgrade -1 && alembic upgrade head
# clean round-trip, no drift (test_alembic_metadata_has_no_drift passes)
$ .venv/bin/python3 -m ruff check <changed files>
# no new findings; pre-existing repo-wide findings unchanged
```

新測試：`tests/unit/test_step19e_etf_split_adapters.py`（11 個測試），使用
2026-09-17 實際抓到的真實 fixture：`twse_twtcau_2020_2026.json`（11 列，包括真實的
空白方向異常）、`tpex_etfsplitrslt_2020_2026_empty.json`、
`tpex_etfrvsrslt_2020_2026_empty.json`（兩者都是真實的，`totalCount: 0`）。

## 已知限制／延後的工作

- TPEx `etfSplitRslt`／`etfRvsRslt` adapter 目前還無法解析真實的列——這是刻意的設計
  （ADR-0023 §3）。等到任一種資料列出一筆的那天，必須先對照那個真實回應驗證它的
  `詳細資料` schema，才能實作 `_row()`；猜測 `pvChgRslt` 的 schema 已被否決。
- `TWTCAU` 的 `old_shares`／`new_shares` 對這個來源永遠保持 NULL；Step 25 的還原因子
  工作必須從 `close_before`／`official_reference_price` 推導 0050 這類 ETF 的分割因子，
  方式與它對現金股利和除權已經採用的相同（CLAUDE.md §80），而不是從這個資料從未發布
  的股數。
- **任何區間跨過 2026-03-31 的未來 `TWTCAU` 請求都會直接失敗**，而不是偶爾失敗——
  00631L 的空白方向儲存格是那天回應的永久特徵，不是暫時的故障。這個 PR 的 backfill
  以手動在那天拆分區間來繞過它（ADR-0023 §4）；未來不知道要這樣做的自動化工作——
  `CorporateActionBackfill` 形式的逐年執行、correction-check 重新抓取，或 Step 27 的
  前向抓取，看哪一個先請求跨過那天的區間——會把整個區間送進 quarantine，並默默地
  永遠不儲存 00674R（2026-04-22）或 00685L（2026-07-07），直到有人注意到 quarantine
  並手動重新拆分。在這裡標記出來（#27 的 code review），讓下一個自動化 `TWTCAU` 區間
  工作的 PR 先讀到這段，而不是在實際執行時重新發現。
