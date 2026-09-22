# Step 24-a 驗收報告

狀態：IN REVIEW (#42)

範圍：TDCC 股權分散的第一部分，adapter 與匯入路徑。這個 step 新增：

- migration `b2c5f8d1a437`（宣告來源 `tdcc_opendata` 與 `tdcc_weekly@1`）
- migration `c9a4e7b21d58`（占比上限改為逐角色）
- adapter `TDCCOpenDataAdapter`（OpenData `getOD.ashx?id=1-5`）與
  `LegacyTDCCArchiveAdapter`（同一個來源，`legacy_archive` 位元組）
- fetcher `TDCCArchiveFetcher`（一週多個副本時以確定的順序選一個並記錄全部）
- importer `TDCCShareholdingImporter`
- CLI `tdcc-shareholding`
- 執行期依賴 `py7zr`
- ADR-0024

376 週的 backfill、每週 coverage cadence、農曆年休市分類與舊系統對帳是 24-b。

## 來源調查（2026-09-22）

428 個檔案全部解壓逐列解析，共 1,377,971 個 security-week。結果寫進
audit §4.9 的「Step 24-a findings」。四項與既有文件不同：

1. 檔案庫已成長為 **428 個檔案／376 個內容日期**（2019-06-28 → 2026-09-18），
   v1 期間內 **349 週**；新的一週以 OpenData 原生檔名加入。
2. 檔名有三種形狀，三種都與內容日期一致；`20210806.7z` 另含一個目錄與四個
   scraper log。
3. `20231020.7z` 是在 1.5 MiB 邊界截斷的下載，比前後週少 562 支證券，無法補回。
4. 分級 16 的官方語意（說明4）、官方的負號，以及 bulk 檔人數欄沒有官方定義。

兩個既有契約假設因此被推翻並修正，理由見 ADR-0024：合計占比可以超過 100
（158 列，最高 135.00），以及 bulk 檔分級 16 的人數欄不入庫。

## Schema 影響

`tdcc_distribution.ownership_percent` 的表層 CHECK 從 `BETWEEN -100 AND 100`
改為 `>= -100`；「holding 不得超過 100」移入
`stockdc_validate_tdcc_distribution_row`（知道角色）與 writer 的 profile 驗證。
沒有新表、沒有新欄位、沒有識別變更。目前沒有任何 `tdcc_distribution` 列，
所以沒有既有資料受影響。

downgrade：`b2c5f8d1a437` 在該來源已有版本／ingest run／manifest 時於修改前拒絕；
`c9a4e7b21d58` 在任何已存列的占比超過 100 時於修改前拒絕（§81）。

## 驗收條件

| 條件 | 結果 | 證據 |
| --- | --- | --- |
| 每一種真實載體都解析為同一份分布，且以內容日期為 key | **PASS** | `tests/unit/test_step24a_tdcc_adapters.py`：`test_a_zip_and_a_7z_read_the_same_as_the_csv`、`test_a_7z_holding_stray_scraper_logs_still_finds_the_one_csv`；真實檔案實測見下 |
| 檔名與內容日期不一致的檔案被拒絕並指出事實 | **PASS** | `test_the_content_date_decides_and_a_disagreeing_name_is_refused`（真實的 `20200619.CSV`，內容 20200612，`snapshot_date_mismatch`；問對週就解析） |
| 分級 1-15 照發布值存 | **PASS** | `test_one_week_parses_every_level_as_published`、整合測試 `test_one_week_imports_sealed_snapshots_for_the_securities_we_hold` |
| 分級 16 存負值、`holder_count` 為 NULL、無官方定義的人數只計數 | **PASS** | `test_the_adjustment_row_is_stored_negative_without_a_holder_count`（`-4000`、`holder_count is None`、`dropped_adjustment_holder_counts == 2`）；整合測試查到 `levels["16"].shares == -1000` |
| 分級 17 照發布值存，含超過 100 的占比 | **PASS** | `test_the_published_total_may_exceed_one_hundred_percent`、整合測試 `test_a_published_total_above_one_hundred_percent_is_stored_as_published`（`00673R` 2020-04-30 存入 101） |
| holding 的 100% 上限沒有消失，只是搬到角色上 | **PASS** | `test_a_holding_level_above_one_hundred_percent_is_rejected`（`TDCCDistributionError: cannot own`） |
| 無法構成完整分布的證券自己隔離，同週其他證券照常匯入 | **PASS** | `test_a_truncated_file_imports_its_complete_securities_and_quarantines_the_cut`、整合測試同名（`import_quarantine` 一列 `incomplete_distribution`、只有 2330 入庫、manifest `truncated_payload=true` 與一筆 warning） |
| 不註冊任何證券；不在 v1 宇宙內的代號只計數 | **PASS** | `test_a_security_outside_the_v1_universe_is_counted_not_registered`（`security` 表只剩 2330，manifest 記 2 個代號） |
| 同一週重跑不產生假修訂與重複證據，重覆抓取仍可稽核 | **PASS** | `test_rerunning_the_same_week_creates_no_revision_and_no_evidence`（版本 3、證據 3 不變，observation 由 3 增為 6） |
| 業務內容變了就是新的 sealed 版本 | **PASS** | `test_a_changed_distribution_is_a_new_sealed_version`（只有改動的那支證券多一個版本） |
| 每週在 `tdcc_weekly@1` 之前 Market-PIT 不可見 | **PASS** | `test_a_week_is_market_invisible_until_its_release_rule`（2026-09-20 12:00 台北之前 `None`，之後可見，evidence_source `tdcc_weekly@1`；System PIT 一直可見） |
| `first_capture` 晚於規則時只寫 capture_bound | **PASS** | `test_a_first_capture_run_claims_its_own_capture_bound`（只有 `capture_bound`，被推翻的規則不寫入） |
| 截斷週重跑安全，且每次抓取的隔離事實各留一筆 | **PASS** | `test_rerunning_a_truncated_week_is_safe_and_records_each_sighting`（版本 1、隔離 2） |
| 檔案庫讀出的位元組記為 `legacy_archive` | **PASS** | `test_the_archive_adapter_records_legacy_archive_provenance` |

### 真實檔案實測（不在測試套件內，指令留在報告裡）

七個真實載體用 `parse_shareholding` 逐一解析：

```text
2019/20190628.zip              2019-06-28 rows=2689 rej=0 zip member=20190628.csv fmt=YYYY/MM/DD
2020/20200103.csv              2020-01-03 rows=2774 rej=0 csv                     fmt=YYYYMMDD
2020/20200430.csv              2020-04-30 rows=2788 rej=0 csv （雙 BOM）          fmt=YYYYMMDD
2021/20210806.7z               2021-08-06 rows=2977 rej=0 7z  member=20210806.csv （另有 4 個 log）
2022/20221125.7z               2022-11-25 rows=3206 rej=0 7z  member=20221125.CSV
2023/20231020.7z               2023-10-20 rows=2786 rej=1 7z  truncated=True（8162 隔離）
2026/TDCC_OD_1-5_20260918.csv  2026-09-18 rows=4067 rej=0 csv
```

一個真實週匯入 `stockdc_backfill`（2026-09-11，`--purpose gap_fill`）：

```text
manifest              succeeded
normalized_row_count  4,055   檔案裡完整的證券數
business_version_count 2,410  在 v1 宇宙內、寫入並封印
securities_outside_the_v1_universe  1,645
tdcc_distribution     40,970 列
evidence              2,410，全部 release_rule
published_at          2026-09-13 04:00+00 = 次週日 12:00 台北
dropped_adjustment_holder_counts    64
rejected_quarantined_count          0
耗時                  27 秒（單週）
```

單週 27 秒代表 24-b 的 376 週約 2.8 小時，與其他領域的 backfill 同量級。

## 測試

```text
.venv/bin/python -m pytest -q
1,145 passed, 3 skipped
```

新增 `tests/unit/test_step24a_tdcc_adapters.py`（21）、
`tests/integration/test_step24a_tdcc_ingestion.py`（11）。
修改 `tests/unit/test_phase6_contract.py`：占比超過 100 現在可表示，
scale 與下界的拒絕不變。

fixture 全部是真實檔案的切片，列保持位元組原樣：
`tdcc_od_1_5_20260918.csv`、`..._20190628_slashed.csv`、
`..._20200430_double_bom.csv`（含 `00673R` 的 101.00）、
`..._20231020_truncated.csv`、`..._20200612_named_20200619.csv`。

migration：`alembic upgrade head` 在乾淨資料庫與 `stockdc_backfill` 都通過；
`test_phase1_schema.py` 的 upgrade/downgrade round-trip 通過。

## 已知限制與刻意延後

- **2023-10-20 永久少 562 支證券。** 沒有任何官方端點能重新取得，
  `shareholding.bak` 的副本被過濾兩次（1,777 支、每支只剩分級 1-15）。
  24-b 的 coverage 報告要把它列為有記載的來源缺陷。
- **bulk 檔分級 16 的人數欄不入庫。** 沒有官方定義，只在 manifest 計數並留在
  raw artifact；哪天有官方定義可以另加欄位。
- **沒有 `dataset_expected_coverage` 列。** 現行 cadence 只允許 `trading_day`
  與 `calendar_month`，每週的 cadence 與涵蓋驗證是 24-b。
- **入口網站的個股查詢沒有實作。** 它只在約一年的時間窗內有資料，用途是修補，
  audit §4.9 已記載；v1 的歷史不依賴它。
- **前向抓取排程**仍是 Step 27。
