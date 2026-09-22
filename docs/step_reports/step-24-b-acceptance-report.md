# Step 24-b 驗收報告

狀態：IN REVIEW (#43)

範圍：TDCC 股權分散的第二部分，376 週的 backfill、涵蓋範圍與舊系統對帳。
這個 step 新增：

- migration `d3b8c6f1a294`（cadence `trading_week` 與 TDCC 的 coverage 宣告）
- `CoverageValidator`／`ExpectedCoverageService` 的每週分支
- backfill runner `TDCCArchiveBackfill`
- CLI `tdcc-shareholding-backfill`
- `scripts/reconcile_tdcc_shareholding.py`
- ADR-0025

## 為什麼是「週」而不是「哪一天」

資料日期是集保的營業日，不是交易所的交易日。376 週實測：

```text
週五 330   週四 22   週六 14   週三 9   週二 1
```

十四個週六沒有一個是 TWSE 交易日，也沒有任何一筆當日行情——它們是補班日。
另有兩個 ISO 週各有兩個資料日期（2020-W39、2021-W07）。反向的情形同樣存在：
2021-W06 交易所整週沒開，集保仍在 2021-02-09 發布。

所以 cadence 是 `trading_week`：有交易日的 ISO 週要有快照，沒開市的週不期待
但要列出來，集保在沒開市的週發布則列為 `unexpected`（ADR-0025）。原本
ROADMAP 的「10 天以上間隔＝農曆年」判準因此被取代：那六個間隔不是週序列的
缺口，只是農曆年週裡資料日期位置異常。

## Backfill

```text
.venv/bin/python -m stock_data_center.ingestion.cli \
    --database-url .../stockdc_backfill \
    tdcc-shareholding-backfill --snapshot-date 2019-06-28 --through 2026-09-18
```

```text
requested_weeks                   376
imported                          376     quarantined 0   failed 0
securities_parsed             1,231,347   檔案裡的完整證券，含 v1 宇宙外
business_versions_created       814,144
business_versions_deduplicated   12,651   24-a 已單獨匯入過的週
row_quarantined_count                 1   2023-10-20 的 8162
truncated_weeks           ['2023-10-20']
is_complete                      True
耗時                        約 2 小時 47 分（約 26 秒／週）
```

資料庫狀態：

```text
tdcc_snapshot_versions      826,795（全部封印）
tdcc_distribution        14,055,515
publication_evidence        826,795，全部 release_rule
raw artifact observations       382，artifact_origin 全部 legacy_archive
ingest_runs purpose             382，全部 gap_fill
來自 shareholding.bak 的路徑        0
```

`purpose` 全是 `gap_fill` 是 24-a 那道修正在真實規模上的驗證：檔案庫路徑不論
宣告什麼都不主張初見，所以沒有任何一週把 2026 年的讀檔時間寫成 capture bound。

## 驗收條件

| 條件 | 結果 | 證據 |
| --- | --- | --- |
| 376 週全部匯入，每週以其內容日期為 key | **PASS** | backfill manifest：requested 376／imported 376／failed 0；`tdcc_snapshot_versions` 有 376 個 distinct `snapshot_date` |
| 有交易日的 ISO 週都要有快照 | **PASS** | coverage 報告：expected 345、observed 345、**missing []** |
| 交易所整週未開的農曆年週不算缺口，但要列出 | **PASS** | `closed_weeks = 2021-02-08, 2022-01-31, 2023-01-23, 2025-01-27, 2026-02-16`，都不在 missing 裡 |
| 2021-W06 列為 unexpected，不過濾 | **PASS** | `unexpected_weeks = ["2021-02-08"]`（集保 2021-02-09 發布、交易所整週未開） |
| 2026-07-09 匯入完整的 4,003 支證券檔案 | **PASS** | 該週 manifest：`complete_securities=4003`、`securities_written=2402`、`archive_file=.../2026/20260709.7z`，不是 1,849 支的重建版本 |
| 不從 `shareholding.bak` 讀取任何東西 | **PASS** | `raw_artifact_observations` 裡 `shareholding.bak` 路徑 0 筆；382 筆全部指向 `data/raw/shareholding` |
| 2023-10-20 列為有記載的截斷週，不默默補平 | **PASS** | backfill manifest `truncated_weeks=['2023-10-20']`、`row_quarantined_count=1`；該週 2,786 支完整證券匯入，8162 進 `import_quarantine` |
| 舊系統對帳：共有的 `(週, 證券, 分級 1-15)` 完全相同 | **PASS** | **9,512,610** 個比對列，**0 個值差異**（人數、股數、兩位小數占比） |
| 對帳要說明舊系統的過濾 | **PASS** | 舊系統只有分級 1-15、無 16／17；宇宙一路比檔案小，2023-09-15 之後縮到 1,767 支。差異逐類分開，見下 |

### 舊系統對帳細節（2020-01-02 → 2026-09-11）

```text
週數        我們 348   舊系統 340   共有 340   舊系統獨有 0
我們多出的 8 週：
  2020-09-26, 2021-02-20   補班週六，舊系統沒有
  2021-02-09               交易所整週未開的那一週
  2021-12-03, 12-10, 12-17, 12-30   舊系統 2021-11-26→12-24 的缺口
  2023-02-04
```

證券層面的差異分成兩類，各自證明：

```text
舊系統有、我們沒有        1,105 支
  其中只出現在截斷週      131 支  → 2023-10-20 的來源缺陷（已記載）
  其餘                    974 支  → 我們從未對其中任何一支有過行情
                                    （also_priced_by_us = []），
                                    即 TDCC 為 v1 宇宙外的代號編表
我們有、舊系統沒有          868 支  → 舊系統的過濾，2023-09-15 之後尤其明顯
```

對帳窗口止於 2026-09-11，與 CLAUDE.md §78 一致。2026-09-18 那一週已匯入，但
交易日曆只到 2026-09-15，coverage 報告因此拒絕對它下結論——護欄運作正常，那
一週等日曆推進後才進得了報告。

## 測試

```text
.venv/bin/python -m pytest -q
1,164 passed, 3 skipped
```

新增 `tests/integration/test_step24b_tdcc_backfill.py`（9）：walk 的週列舉
（三種檔名、重複副本、`_quarantine`、無日期檔名）、逐週 import id 與續跑、
單週隔離不終止整趟、截斷週在報告裡具名、每週 cadence 的四種情形（期待、
休市週、缺口、交易所未開卻有快照）。

## 已知限制與刻意延後

- **2023-10-20 永久少 562 支證券**，其中 131 支只在該週出現。沒有任何端點能
  重新取得。
- **coverage 只能推進到交易日曆的邊界**，所以最新一週要等 Step 16 的日曆前進。
- **每週的「哪一天」仍然不可預測**。若日後取得集保自己的營業日曆，可以在不改
  儲存與既有報告語意的前提下換成更精確的 cadence（ADR-0025）。
- **2019-06-28 到 2019-12-27 的 27 週**匯入了，但落在 v1 視窗之前，coverage
  宣告的 `window_start` 是 2020-01-02，所以不在報告範圍內。
