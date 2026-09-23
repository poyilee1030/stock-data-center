# Step 35-c-1 驗收報告

狀態：IN REVIEW (#48)

範圍：ADR-0027「35-c 定案」的第一段。建立月營收、財報、TDCC、公司行動的 v2 表，
把前三者的 v1 歷史搬過來並逐列驗證。寫入路徑（35-c-2、35-c-3）與衍生資料（35-c-4）
不在這一段；v1 表與其 ingestion 不動，35-d 才一次刪除。

## 交付

- `src/stock_data_center/db/schema_v2.py`：5 張表 `monthly_revenues`、`financial_reports`、
  `financial_report_facts`、`shareholding_distributions`、`corporate_actions`
- migration `76245e1b428b`：建表與 append-only 觸發器；降級在任何一張有資料時先拒絕
- `src/stock_data_center/v2/release_rules.py`：`tdcc_weekly@1`、`corporate_action_ex_date@1`，
  各有 Python 與 SQL 兩種算法
- `scripts/migrate_to_schema_v2.py`：新增月營收、財報、財報事實、TDCC 四個搬移步驟
  （可續跑，已有資料的表跳過）
- `scripts/verify_schema_v2_35c.py`：逐列驗收（只讀）
- `docs/data_domain_inventory.json` 與 audit §5：每個新欄位的來源登記
- 測試：`tests/integration/test_schema_v2_35c_tables.py`（26）

## 與 ADR 不同的一處

財報事實表在 ADR 討論時叫 `financial_facts`，但 v1 已有同名表，而 v1 與 v2 共用一個
metadata，所以 v2 的叫 `financial_report_facts`（與 `financial_reports` 成對）。ADR 與
ROADMAP 已同步改名。

## 驗收

| 標準 | 結果 | 證據 |
|---|---|---|
| migration 建 5 張表，只新增 | PASS | UPDATE／DELETE 每張表都被觸發器拒絕（10 條測試）；`alembic check` 無差異 |
| 降級在有資料時拒絕 | PASS | 有一列時降級失敗、版本不變；空的時候可降級再升級 |
| 兩條 release rule 為程式常數 | PASS | Python 與 SQL 算法逐日相同（含週日資料日要等一整週） |
| 每張表與 v1 篩選結果雙向比對 0 差異 | PASS | 見下表 |
| 可見時間與 v1 的證據逐列一致（公司行動除外） | PASS | TDCC 規則時刻 692,660 週 0 週與 v1 證據不同；月營收、財報的 `published_at` 納入上面的雙向比對 |
| 月營收更正過的 key，最新一列是 MOPS 的值 | PASS | 116 個 key 0 個例外 |

`scripts/verify_schema_v2_35c.py`（`stockdc_backfill`，只讀，273 秒，退出碼 0）。比對的另一邊
不經過搬移用的 SQL：TDCC 是把 v2 寬表還原成 17 列長表，再和 v1 的 `tdcc_distribution` 比。

| 表 | 列數 | v1 有、v2 沒有 | v2 有、v1 沒有 | 大小（v1 → v2） |
|---|---:|---:|---:|---|
| `monthly_revenues` | 149,191 | 0 | 0 | 37 MB |
| `financial_reports` | 42,417 | 0 | 0 | 7 MB |
| `financial_report_facts` | 16,062,417 | 0 | 0 | 10.0 GB → 5.8 GB |
| `shareholding_distributions` | 692,660 | 0 | 0 | 1.7 GB → 0.37 GB |
| `corporate_actions` | 0 | — | — | 35-c-3 才抓 |

- 月營收：149,191 列中 139,317 列有 `published_at`；其餘是 KY 公司與更正後的值，照 v1 不可見。
  116 個 key 有兩列：legacy 首次抓取的值在前、MOPS 目前的值在後。
- 財報：42,417 份全部有 `published_at`。
- 只收今天 `stocks` 內的 1,947 檔：v1 共 150,757／42,750／826,795 列，少掉的是已下市或不是普通股的。
- 事實表比 ADR 估的約 4 GB 大：唯一索引包含完整的 concept 文字。

全套測試：1260 passed、3 skipped（需 `RUN_LIVE_SOURCE_TESTS`）。`src/` 新增 271 行。

## 踩到的坑

- **原本以為**驗證可以用逐列的相關子查詢去 `publication_evidence` 找公開時間。實際上
  那張表有 2,200 萬列、沒有依版本 id 的索引，15 萬列的月營收跑了 12 分鐘還沒完。改成
  先以 `dataset_code` 篩選再 join。
- **原本以為**事實表會降到約 4 GB，實際 5.8 GB，多出的是唯一索引。
- **原本以為**要建 6 張表（ROADMAP 初稿的數字）。實際是 5 張；衍生資料 0 張。
- `pkill -f <腳本名>` 會把下這個指令的 shell 自己也殺掉（35-b-1 記過的 `pgrep -f` 同一族）；
  停止背景查詢改用 `pg_cancel_backend`。

## 延後

- 35-c-2：月營收、財報、TDCC 的寫入路徑
- 35-c-3：公司行動的寫入路徑與 2020–2026 回補
- 35-c-4：26-a 改接 v2
