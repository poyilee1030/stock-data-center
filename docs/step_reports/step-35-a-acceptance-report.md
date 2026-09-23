# Step 35-a 驗收報告

狀態：IN REVIEW (#46)

範圍：schema v2 的第一步（ADR-0026、ADR-0027）。新增基礎表與交易所每日資料表，
抓取今天的普通股清單，把 v1 歷史複製過來並逐列驗證。v1 表與其 ingestion 不動，
它們在 35-b 改寫後才 drop。

## 交付

- `src/stock_data_center/db/schema_v2.py`：11 張表，註冊在同一個 `metadata` 上
- migration `272951c4e3eb`：建表、append-only 觸發器；降級在任何 v2 表有資料時先拒絕
- `src/stock_data_center/v2/`：`fetch_log.py`（raw-first 記錄抓取）、`universe.py`
  （ISIN「股票」清單）、`indices.py`（保留的 126 個指數）
- `scripts/migrate_to_schema_v2.py`：受信任的遷移路徑，可續跑
- `docs/data_domain_inventory.json` 與 audit §4.14、§5：每個 v2 欄位的來源登記
- ADR-0026 Accepted、ADR-0027、ADR-0023 Superseded；ROADMAP Step 35、Step 34
  Superseded；CLAUDE.md §0

## 設計決策的出處

每張表與欄位的去留，都是 2026-09-23 與 owner 逐張檢討「需不需要、拿掉會損失什麼」
的結果，記在 ADR-0027。幾個以資料決定的點：

- **型態**：依 2020–2026 實測。價格最大 19,880、最多 2 位小數 → `numeric(10,2)`；
  指數收盤最大 420,878（報酬指數）→ `numeric(12,2)`；本益比最大 24,950；比率
  最大 100.00。`price_direction` 除了 `+ - X` 還有 `flat`，所以是 `varchar(4)`。
- **淨額與餘額保留**：法人淨額（312 萬列 0 筆不符）、融資融券餘額（313 萬列 0 筆
  不符）都可以精確算出，owner 決定比照 legacy 照官方數字存。
- **外資持股的比率保留**：不能自己算，官方用無條件捨去（TWSE、MOPS）或四捨五入
  （TPEx `insti/qfii`），約一半的列和簡單除法不同。
- **上櫃外資持股只留 MOPS**：legacy 用的就是它；當初加 TPEx 來源的理由（MOPS
  依今天的清單重建過去）在「以今天為準」之後不存在了。

## 遷移結果（`stockdc_backfill`，2026-09-23）

```text
fetches                      77,332   v1 ingest run 一對一，沿用 UUID
stocks                        1,947   上市 1,054 + 上櫃 893
trading_days                  1,629   2020-01-02 → 2026-09-15
daily_prices              2,872,469
valuations                2,872,606
institutional_flows       2,600,961
foreign_holdings          2,868,486
margin_trading            2,656,183
securities_lending        2,699,567
index_prices                184,349   126 個指數
institutional_market_flows   22,778
```

v2 合計約 4.1 GB；它取代的 v1 表連同 observations 約 14 GB，另有它們在
`publication_evidence` 中的證據列。`daily_prices` 735 MB，legacy `daily_quotes`
865 MB。

## 驗證

| 驗收條件 | 結果 |
| --- | --- |
| migration 升級、降級、`alembic check` | **PASS**。空資料庫往返成功；`alembic check` 無差異；有資料時降級先拒絕 |
| 六張個股表與 v1 篩選結果一致 | **PASS**。`EXCEPT` 雙向比對全部 0 差異（含 `recorded_at` 與 `fetch_id`） |
| 指數與市場彙總 | **PASS**。126 個指數；市場彙總 22,778 列與 v1 相同 |
| 只新增 | **PASS**。`test_a_value_row_is_append_only`：UPDATE、DELETE、TRUNCATE 皆被拒絕，含 `fetches` |
| 更正是新列 | **PASS**。`test_a_correction_is_a_second_row_not_an_edit` |
| 來源登記完整 | **PASS**。storage-contract 測試涵蓋全部新表與欄位 |
| 全套測試 | **PASS**。1,177 passed、3 skipped（live source）；新增 11 條（4 unit、7 integration）。`stockdc_backfill` 上 `alembic check` 無差異 |

## 遷移中發現的事

- **v1 的修訂只有一支證券。** `margin_trading_versions` 有 612 個 key 有兩個版本，
  全部是 `008201` 在 2026-09-19 的單位更正（1,000 → 100）。它不是普通股，不在範圍
  內，所以複製的列裡每個 key 都只有一個版本；腳本仍會在未來遇到多版本時拒絕，
  而不是自行選一個。
- **兩支普通股沒有行情**：2938 床的世界（2026-09-16 上櫃）、7856 漢測（2026-09-22
  上櫃），都在回補期間之後才掛牌，這是正確的。
- **舊的抓取狀態對應**：v1 `succeeded` 74,355、`failed` 2,975、`running` 2。v2 中
  有隔離原因的失敗成為 `quarantined`，兩筆中斷的成為 `failed`（`interrupted`）。

## 已知限制

- **倖存者偏差**：今天之前下市的公司全部不收（ADR-0026，owner 接受）。
- **v2 還沒有 ingestion 與 resolver**：新資料目前仍寫到 v1 表；35-b 改寫。
- **`trading_days` 的休市判斷**要靠 `fetches` 裡該月份的成功抓取，查詢函式在 35-b 一併提供。
- **`stockdc_backfill` 的 26-a migration 已降級**：它不在這條分支的鏈上；26-a 在
  衍生資料的重新設計時改接 v2。
