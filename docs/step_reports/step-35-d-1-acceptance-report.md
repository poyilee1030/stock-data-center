# Step 35-d-1 驗收報告

狀態：MERGED (#52)

範圍：讓 schema v2 不再載入任何 v1 模組，35-d-2 才能刪除 v1 程式而不動到 v2。只搬、不刪：定義一字不改，
原本的 v1 模組改從新位置 import，v1 照常運作。資料庫不動。

## 開工時的量測

v2 的每個模組 import 遞移下去，載入 **73 個 v1 模組、21,930 行**（`src/` 共 31,661 行）：

- adapter 產出的 observation 型別（`DailyPriceObservation`、`MonthlyRevenueObservation`、
  `TDCCSnapshotObservation`、`CorporateActionObservation`…）定義在 v1 的領域套件裡，那些套件的
  `__init__` 又載入 v1 的 writer、service 與 PIT resolver
- `ingestion/__init__.py` 載入 v1 的 importer；`db/__init__.py` 載入 v1 的 61 張表宣告；`schema_v2` 的表
  掛在 `db/metadata.py` 的 MetaData 上
- iXBRL parser 在 `financials/`；`current_git_commit` 在 v1 的 `ingestion/lifecycle.py`

## 交付

| 搬了什麼 | 從 | 到 |
|---|---|---|
| 44 個頂層定義：observation 與值型別，連同它們用到的常數與 helper（原樣複製） | `market_data`、`institutional_financing`、`market_reference`、`monthly_revenue`、`tdcc`、`financials` | `ingestion/observations.py`（790 行） |
| iXBRL parser | `financials/ixbrl.py` | `ingestion/ixbrl.py`（git rename；舊路徑留一個指向同一個模組物件的 shim） |
| MetaData、命名規則、欄位型別 | `db/metadata.py` | `db/base.py` |
| `current_git_commit` | `ingestion/lifecycle.py` | `v2/fetch_log.py` |

- `ingestion/__init__.py`、`db/__init__.py` 不再載入任何東西；用到它們 re-export 的 4 個 v1 測試檔與 6 個 v1
  模組改從子模組 import
- adapter 與 v2 改從新位置 import
- `src/` +1,737／−1,734 行，幾乎全是搬移

搬移用一支 AST 腳本做：對每個要搬的名稱，取它的原始碼區段，連同它引用到的同模組 module-level 定義一起遞迴
帶走，再在原模組留下 import。沒有手改任何一個定義。

## 驗收

| 標準 | 結果 | 證據 |
|---|---|---|
| v2 不載入任何 v1 模組 | PASS | `test_v2_loads_no_v1_module`：在全新的 interpreter 載入每個 v2 模組，所有載入的 `stock_data_center` 模組都在保留清單上（v2、`db.base`、`db.schema_v2`、`provenance`、`ingestion` 的 adapters／http／ixbrl／models／observations／raw_storage）。實作前它列出 48 個不在清單上的模組 |
| v2 的表不帶 v1 的表 | PASS | `test_the_v2_tables_are_declared_without_the_v1_ones`：只 import `schema_v2` 時 MetaData 上沒有 `security`、`publication_evidence`、`daily_price_versions` |
| v1 照常運作 | PASS | 全套 1334 passed、3 skipped（v1 測試全部照過）；repo 內每一個 `from stock_data_center… import …` 都解得開 |
| schema 不變 | PASS | `alembic check`：No new upgrade operations detected |
| lint 不變差 | PASS | 改動的每個檔案，ruff 的發現數都不多於 main；`ixbrl.py` 的 39 項與 `observations.py` 的 2 項是隨定義一起搬過來的既有項目 |

### v2 的真實行為不變

用前幾步入庫的驗收腳本重跑，與各自的基線逐項比對：

| 腳本 | 範圍 | 結果 | 基線 |
|---|---|---|---|
| `verify_v2_write_path.py`（35-b-1） | 3 個交易日 × 16 個交易所 job，真實抓取 | 0 列不同 | 0 列不同 |
| `verify_v2_35c_write_path.py`（35-c-2） | 3 個月的月營收、TDCC 最新一週、4 份財報，真實抓取 | 0 列不同；7856 另列 | 相同（7856 是 09-22 上櫃的新股） |
| 公司行動一般回補（35-c-3） | 6 個 feed，2020–2026 | 每個 feed 跳過 6 年、0 列新增、0 撤回、0 擋下 | 相同 |
| `technical_indicators:v1` digest（35-c-4） | 1,959 條序列 | 與 v1 對照組 0 條不同 | 0 條不同 |

## 踩到的坑

- **原本以為** v2 只用到 adapter 與幾個小模組。實測 import 遞移載入了七成的 `src/`：套件的 `__init__` 會連帶
  載入整個套件，observation 型別又住在 v1 的領域套件裡。
- 搬 `SourceContextClassification` 之後出現循環 import：`ingestion/__init__` → `models` → `financials` →
  `ingestion.observations`。清空 `ingestion/__init__` 之後就消失了。
- `financials/ixbrl.py` 的 shim 一開始用 `import *`：v1 的測試會 import parser 的私有名稱，而 `import *`
  不帶底線開頭的名稱。改成讓 `sys.modules` 指向同一個模組物件。

## 延後

- 35-d-2：刪除 v1 程式、測試與只為 v1 寫的 scripts；保留清單就是本 step 測試裡的 `KEPT`
- 35-d-3：baseline migration、在 `stockdc_backfill` 刪除 v1 表（owner 確認）、文件改寫
