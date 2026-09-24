# Step 35-d-3 驗收報告

狀態：IN REVIEW (#54)

範圍：以一個 baseline migration 重新開始 migration 鏈、刪除 `db/metadata.py` 與所有 v1 表，並把 CLAUDE.md、
domain inventory、source audit 與各份 `docs/*.md` 裡 v1 專屬的內容改寫成 schema v2。拆 a／b 兩步的提議
owner 否決，一個 PR 做完（2026-09-24）。

## 改了什麼

| | 內容 |
|---|---|
| migration | 刪除舊鏈 45 個 migration；新的 baseline `a273160c0288` 建 16 張 v2 表、`stockdc_reject_mutation()` 與 14 張表的 28 個 trigger，不建任何 extension、view 或 sequence。降級在任何表有資料時先拒絕，空的時候全部刪掉 |
| `src/` | 刪除 `db/metadata.py`（1,976 行）；`migrations/env.py` 改讀 `schema_v2.metadata`；兩處把 v1 表講成現在式的註解改正 |
| 舊鏈資料庫 | `scripts/rebase_to_baseline.py`：把停在舊 head `5c1e8d2a7b90` 的資料庫轉到 baseline（見下） |
| 測試 | 刪除 v1 的 schema 測試 `test_phase1_schema.py`、`test_pr16_trading_calendar_schema.py` 與 35-c-1 對舊 revision 的兩條降級測試；新增 `test_schema_v2_baseline.py`（6 條）與 `test_rebase_to_baseline.py`（5 條）；`test_step15a_metadata_ddl.py`、inventory 與文件契約測試改成 v2 |
| inventory | `data_domain_inventory.json` 416 個舊系統欄位全部改指 v2 的表與欄位，每個 observed／identity／publication_time 目標都驗證存在於 v2 metadata；`storage_contract` 只留 v2 的 15 張表與 `fetches`；`publication_evidence` 處置改名 `publication_time`；`.md` 整份改寫 |
| audit | §5 只留 v2 的 9 個部分來源欄位，v1 的 32 列刪除（v2 本來就不存無來源的欄位）；§4 的欄位表與「每個欄位都有來源」改成 v2 名稱；§7 說明 v1 的證據如何變成 v2 的 `published_at` |
| CLAUDE.md | §0 的覆寫清單併回各條；§15–§33、§42、§51、§66、§71–§79 改寫成 v2 的規則；§21、§22 保留條號、註明隨 seal 移除 |
| docs | `cache.md`、`pit_resolver.md` 刪除（程式已不存在）；`schema.md`、`pit_semantics.md`、`architecture.md`、`derived_data.md` 與 7 份領域文件改寫成 v2，保留來源語意（單位、錨點、級距、identity 規則）；README 的狀態與已知問題改成現況 |

## 舊鏈資料庫怎麼轉

`stockdc_backfill` 有六年的 v2 歷史，停在舊鏈的 head，旁邊是 v1 的表。不寫第 47 個 migration，而是用腳本轉：

1. 在同一台伺服器建一個暫存資料庫、升級到 baseline，取它的簽章（每張表的欄位、型別、預設值、約束、索引、
   trigger、函數本體、view、sequence、extension），再刪掉暫存庫。「v1 物件」就是目標庫有、這份簽章沒有的
   東西，不靠手寫清單。
2. 同一個交易裡：確認版本是舊 head、baseline 的物件一個不少，記下每張 v2 表的列數；以不加 CASCADE 的
   `DROP` 刪掉 view、表、sequence、函數、extension；把版本記成 baseline。
3. commit 前再取一次簽章，必須與全新 baseline 逐物件相同，v2 各表列數必須不變；任何一項不符就整筆
   rollback。

不加 `--execute` 只列出會刪什麼。`stockdc_reject_mutation()` 的本體與舊鏈逐字相同，否則第 3 步會擋下。

## 驗收

| 標準 | 結果 | 證據 |
|---|---|---|
| 一個 baseline 重新開始 migration 鏈 | PASS | `test_the_chain_is_one_baseline`：鏈上只有一個 revision，沒有 `down_revision` |
| baseline 只建 schema v2 | PASS | `test_the_baseline_builds_exactly_schema_v2`：public 的表恰為 v2 的 16 張加 `alembic_version`；函數只有 `stockdc_reject_mutation`；extension 只有 `plpgsql`；沒有 view；trigger 恰為 14 張只新增表的 `immutable_*`、`no_truncate_*` |
| 降級不丟資料 | PASS | 有資料時拒絕且版本不動（`test_the_downgrade_refuses_while_a_table_holds_rows`）；空的時候刪光、再升級恢復（`test_an_empty_downgrade_drops_everything_and_upgrade_restores_it`） |
| metadata 與 baseline 一致 | PASS | `alembic check`：No new upgrade operations detected（測試庫與轉換後的 `stockdc_backfill` 都是） |
| `src/` 沒有 v1 模組 | PASS | `test_src_holds_only_what_v2_keeps` 拿掉 `db.metadata` 的例外 |
| 所有 v1 表刪除，`stockdc_backfill` 經 owner 確認 | PASS | 見下方「stockdc_backfill」 |
| inventory／audit／metadata 互相一致 | PASS | `test_pr14_storage_contract_source_coverage.py` 11 條改成 v2，在舊 JSON 上 6 條紅 |
| 測試 | PASS | 700 passed（main 725：刪掉的是 v1 schema 測試，新增 11 條） |
| lint 不變差 | PASS | 每個改動的檔案 ruff 發現數不多於 main；新檔 0 |

### 轉換腳本對真的舊鏈

fixture 只能模擬舊鏈（舊鏈已刪）。所以刪除舊鏈之前，先用 main 的 45 個 migration 建了 `stockdc_rebase_check`，
再用本 step 的腳本轉它：要刪 60 張表、2 個 view、40 個函數、pgcrypto，0 個 sequence、0 個 type；轉完與全新
baseline 逐物件相同，第二次執行回報 `already_rebased`。

測試的反向檢查：對腳本逐一改壞，每一種都有一條測試變紅。

| 改壞 | 變紅的測試 |
|---|---|
| `DROP TABLE … CASCADE` | `test_a_drop_that_would_reach_into_v2_changes_nothing` |
| 拿掉 commit 前的簽章比對 | `test_a_schema_that_differs_from_the_baseline_is_refused` |
| 拿掉版本檢查 | `test_a_database_at_another_revision_is_refused` |
| dry run 也執行 | `test_the_plan_names_every_v1_object_and_changes_nothing` |

列數不變的檢查無法單獨觸發：不加 CASCADE 的 `DROP` 刪不到 v2 的列。它留著，是防將來有人改成 CASCADE 時的
第二道線。

### stockdc_backfill（2026-09-24，owner 確認後執行）

| | 執行前 | 執行後 |
|---|---:|---:|
| 資料庫大小 | 48 GB | 10 GB |
| public 的表 | 77 | 17 |
| 版本 | `5c1e8d2a7b90` | `a273160c0288` |
| v2 各表列數合計 | 33,828,374 | 33,828,374（16 張表逐一相同） |

刪除的是 60 張 v1 表、`visible_financial_filings`、`visible_tdcc_snapshots`、40 個 v1 函數與 pgcrypto，一個交易
2.6 秒。`data/raw/` 沒有動。

### v2 的真實行為不變

與 35-d-2 用同樣的腳本與參數重跑。兩支驗收腳本的暫存資料庫由新的 baseline 建立，所以這也驗證了 baseline
建出的 schema 能跑 v2 的寫入路徑。

| 腳本 | 結果 | 35-d-2 |
|---|---|---|
| `verify_v2_write_path.py`，3 個交易日 × 16 個 job，真實抓取 | 0 列不同；報告與 35-d-2 逐項相同 | 0 |
| `verify_v2_35c_write_path.py`，3 個月月營收、TDCC、4 份財報，真實抓取 | 0 列不同；7856 另列 | 相同 |
| 公司行動一般回補，6 個 feed，在轉換後的 `stockdc_backfill` | 每個 feed 跳過 6 年、0 列新增、0 撤回、0 擋下；報告逐項相同 | 相同 |
| `technical_indicators:v1` digest，1,959 條序列 | 與 v1 對照組（35-c-4 的 26-a service）0 條不同，2,872,469 列；每支 p50 0.10 秒 | 0 |

## 踩到的坑

- **原本以為**舊鏈有 46 個 migration：`ls migrations/versions | wc -l` 把 `__pycache__` 也算進去，實際 45 個；
  v1 表也不是先前記下的 61 張，而是 60 張。文件裡的數字照實數改正。
- **原本以為** pgcrypto 被 `fetches.id` 的預設值 `gen_random_uuid()` 依賴，刪 extension 會連帶拿掉預設值。
  查 `pg_depend`：PostgreSQL 13 起 `gen_random_uuid()` 是內建函數，預設值解析到 `pg_catalog` 的版本，
  與 pgcrypto 無關。腳本仍不加 CASCADE，真有依賴時會失敗而不是悄悄刪掉。
- baseline 的函數本體若縮排不同，舊鏈的資料庫就轉不過來：簽章比的是 `pg_get_functiondef` 的全文。
- 用 `importlib` 載入帶 dataclass 的腳本，要先放進 `sys.modules`，否則 `from __future__ import annotations`
  的 dataclass 解析型別時找不到模組。
- inventory 的測試會把 `storage_contract` 綁到 metadata、audit §5 綁到 `storage_contract`，拿掉 v1 metadata
  後三者要同時改，不能只改其中一份。

## 延後

- 本機還有 v1 時代留下的資料庫（`stockdc_step17`、`stockdc_step17b`、`stockdc_step19d`、`stockdc_step19e`、
  `stockdc_mig`、`stockdc_phase8_review`、`stockdc_pr11_20260913`、`dgtest` 與 18 個 `stockdc_concurrency_*`），
  不在本 step 範圍，沒有動。`scripts/verify_v2_corporate_actions.py` 仍讀 `stockdc_step19d` 當對照組。
- ADR 與舊的 step 報告照原樣保留：它們是當時的決策紀錄，CLAUDE.md §0 註明 ADR-0026／0027 優先。
