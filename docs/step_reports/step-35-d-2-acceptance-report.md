# Step 35-d-2 驗收報告

狀態：IN REVIEW

範圍：刪除 v1 的程式、測試與只為 v1 寫的 scripts。35-d-1 已讓 v2 不載入任何 v1 模組，所以這一步只刪、不改
v2。資料庫不動：v1 的表、`db/metadata.py` 與 migration 鏈留到 35-d-3。

## 刪了什麼

| | 刪除 | 保留 |
|---|---|---|
| `src/` | 72 個模組、17,528 行：v1 的 writer、service、PIT resolver、evidence 政策、coverage、CLI、backfill 與 import lifecycle，連同 `market_data`、`market_reference`、`market_calendar`、`monthly_revenue`、`financials`、`tdcc`、`institutional_financing`、`pit`、`evidence`、`coverage` 整個套件 | 14,136 行：`v2/`、`db/`（`base`、`schema_v2`、待 35-d-3 刪的 `metadata`）、`ingestion/`（13 個 adapter、observations、ixbrl、models、http、raw_storage）、`provenance.py` |
| adapter | 5 個 v2 不用的：Step 9 個股 pilot（`daily_market`）、被 ISIN 名單取代的 `security_lifecycle`／`security_metadata`（ADR-0026）、歷史已由 35-c-1 搬過來的兩個 legacy 檔案庫 adapter | `trading_calendar` 留著：v2 讀 `trading_days` 但還沒有 job 寫它，Step 28 需要 |
| 保留模組裡的死碼 | 19 個沒有任何引用的定義（v1 importer 的 request、parsed、manifest 型別，`EPSPeriodBasis` 等） | |
| 測試 | 53 個檔、22,561 行：v1 的整合與契約測試，以及 5 個被刪 adapter 的測試；23-a、23-b 各刪掉只測 v1 EPS 分類與檔案庫 adapter 的測試函式 | 38 個檔：v2、保留的 adapter、iXBRL parser、HTTP fetcher，以及 v1 表的 schema 測試（35-d-3 刪） |
| fixture | 8 個沒有任何測試引用的檔 | 107 個 |
| scripts | 19 支：v1 的 reconcile／verify、`migrate_to_schema_v2.py`（v1→v2 搬移已完成）、`verify_schema_v2_35c.py`、呼叫 v1 CLI 的 `backfill_financial_filings.sh` | 4 支：`verify_v2_write_path.py`、`verify_v2_35c_write_path.py`、`verify_v2_corporate_actions.py`、`reconcile_technical_indicators.py` |

保留的 adapter 測試原本從 v1 位置 import 型別（例如 `market_reference.models.TwdAmount`），改成從
`ingestion.observations`、`ingestion.ixbrl` import；兩支 v2 驗收腳本的 `current_git_commit` 改從
`v2.fetch_log` import。

分類用一支 AST 腳本：一個測試檔或 script 只要 import 到任何要刪的模組就刪，否則保留；再逐一檢查被刪的清單，
把只是從舊位置 import 型別的改寫後留下。

## 驗收

| 標準 | 結果 | 證據 |
|---|---|---|
| `src/` 只剩 v2 要的模組 | PASS | 新增 `test_src_holds_only_what_v2_keeps`：`src/` 的每個模組都在 35-d-1 的 `KEPT` 清單上（`db.metadata` 例外，35-d-3 刪），且 5 個被刪的 adapter 不存在。實作前它列出 67 個模組 |
| v2 仍不載入 v1 | PASS | 35-d-1 的兩條測試照過 |
| repo 內的 import 全部解得開 | PASS | 對 `src`、`tests`、`scripts`、`migrations` 每一個 `from stock_data_center… import …` 逐一 import：0 個失敗 |
| 測試 | PASS | 725 passed（原 1334，刪掉的都是 v1 測試；3 條 live 測試隨 v1 整合測試刪除） |
| schema 不變 | PASS | `alembic check`：No new upgrade operations detected |
| lint 不變差 | PASS | 改動的每個檔案，ruff 發現數都不多於 main |

### v2 的真實行為不變

與 35-d-1 用同樣的腳本與參數重跑：

| 腳本 | 結果 | 35-d-1 |
|---|---|---|
| `verify_v2_write_path.py`，3 個交易日 × 16 個 job，真實抓取 | 0 列不同 | 0 |
| `verify_v2_35c_write_path.py`，3 個月月營收、TDCC、4 份財報，真實抓取 | 0 列不同；7856 另列 | 相同 |
| 公司行動一般回補，6 個 feed | 0 列新增、0 撤回、0 擋下 | 相同 |
| `technical_indicators:v1` digest，1,959 條序列 | 與 v1 對照組 0 條不同 | 0 |

## 踩到的坑

- **原本以為**「import 到 v1 模組的測試」都是 v1 測試。19-a、21-a、22-a、23-a 等保留 adapter 的單元測試也在
  其中，只是從舊位置 import 型別；照單全刪會讓保留的 adapter 失去測試。改寫 import 後保留。
- 反過來，5 個被刪 adapter 的測試只 import `ingestion.adapters`（保留的套件），分類腳本判成保留，要另外刪。
- `ruff --fix` 對整個 `tests/` 跑會順手修掉 8 個無關檔案的既有 lint；這些改動還原，不混進刪除 PR。
- 靠檔名找沒被引用的 fixture 會誤判：`f"mops_t164sb01_{name}.html"` 這類動態檔名要去掉前綴再找一次。

## 延後

- 35-d-3：baseline migration、刪除 `db/metadata.py` 與 v1 表（`stockdc_backfill` 刪除前 owner 確認）、
  v1 表的 schema 測試、CLAUDE.md／domain inventory／source audit／`docs/*.md` 裡 v1 專屬的內容
