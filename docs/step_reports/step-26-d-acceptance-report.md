# Step 26-d 驗收報告

狀態：IN REVIEW (#57)

範圍：`shareholding_concentration:v1`，移植舊系統 `calculate_shareholding_concentration.py`，沿用 26-b 的增量執行器。

## 交付

| | 內容 |
|---|---|
| schema | `shareholding_concentration`：`large_holder_ratio`、`mid_holder_ratio`、`small_holder_ratio`、`concentration_spread`（double）、`large_holder_count`、`small_holder_count`（bigint）、四個 `_wow`（double），全部可為 NULL，key `(stock_id, source, snapshot_date)`，加 `computed_at`；migration `c3f1f515e3df`，降級不設防 |
| `src/stock_data_center/v2/concentration.py` | 分組、加總與週變化的純函式 |
| `src/stock_data_center/v2/derived_store.py` | 資料集定義與計算；key 的日期欄改成依表而定（`period`）；寫入者的鎖改從 `backfill.JOBS` 取（`writer_keys`） |
| scripts | `reconcile_shareholding_concentration.py`（新）；`verify_derived_store.py` 加上集中度 |
| 測試 | `tests/unit/test_v2_concentration.py`（9）；`test_v2_derived_store.py` 加 9 條；`test_schema_v2_baseline.py` 的衍生表清單 |
| 文件 | ROADMAP §20、Step 26（26-c 標為 MERGED、新增 26-d 小節）；CLAUDE.md 快照；`derived_data.md`、`schema.md`、`tdcc.md`、domain inventory（md／json）、README；26-c 報告標為 MERGED |

`src/` +173／−13 行。

## 每一欄的理由

拿掉這張表，資料正確性沒有損失：比率是同一列 15 個級距的加總，週變化只多讀前一週那一列。owner 仍依 Step 26
的決定存表（2026-09-25），讓每個衍生資料集走同一條讀取路徑。

- 三個比率、兩個人數：舊系統下游讀的值；分組的級距（1–8、9–11、12–15）是定義的一部分，放在 Data Center 才只有一份。
- 三個比率的週變化：唯一不是逐列算得出的欄位，要找同一支股票的前一個快照。
- `concentration_spread` 與它的週變化：可以從大、小戶兩欄逐位算回（輸入是兩位小數的精確值）。owner 選擇照舊系統存。
- 不新增中戶人數：舊系統沒有。舊系統的 `pced_*` 不存。

## 公式

- 小戶是級距 1–8（50 張以下），中戶 9–11（400 張以下），大戶 12–15。比率是各組 `percent_N` 的和，人數是小、大戶
  `holders_N` 的和，spread 是大戶減小戶。
- 週變化對的是同一支股票的前一個快照，不論隔幾週（舊系統的 `LAG`）；第一個快照為 NULL。
- 舊系統以 double 加總，存 `ROUND(x::numeric, 4)`。集保的百分比是兩位小數，精確的十進位和就是那個結果，所以這裡
  精確加總，四捨五入遠離零到四位（實際上不會動到）。`test_exact_sums_equal_legacys_double_precision_ones` 以 5,000
  組隨機兩位小數驗證兩條路逐位相同。
- 舊系統的 `COALESCE(x, 0)` 在它自己的資料上從未觸發（15 個級距的人數與百分比 0 筆 NULL）。這裡來源沒發布的級距，
  讓需要它的組、spread 與週變化為 NULL，不當成 0。
- key 用集保的快照日：`stockdc_backfill` 有 48 個快照日不是交易日（2019 年日曆之前、補班週六 2020-09-26 與
  2021-02-20、交易所整週未開的 2021-02-09）。
- **沒有暖機緩衝**：週變化需要重寫起點之前的那一個快照，一支股票只有數百週，所以每次重寫都從第一個快照讀起，
  增量與整段逐位相同。

## 驗收

| 標準 | 結果 | 證據 |
|---|---|---|
| 與舊系統的表對帳，差異逐一分類 | PASS | 0 筆無法歸因（見下方） |
| 增量與整段重算逐位相同 | PASS | 真實資料：`derived_store.rewrite` 在會 rollback 的交易裡，對 1,946 條序列 × 6 個起點各執行一次、讀回整條序列，1,625,805 列 0 筆不同，起點之前的列都沒被動到。重算起點怎麼選（`_changed`）由整合測試驗證：更正的快照、晚到的較早快照、`test_an_incremental_concentration_equals_the_full_one` |
| 沒有用到未來的資料 | PASS | `test_a_concentration_uses_no_snapshot_dated_after_it`：加入之後的快照、整段重算後，D 之前的值不變；`test_no_value_changes_with_a_later_snapshot` 對純函式驗同一件事 |
| 每張新表的理由 | PASS | 上方「每一欄的理由」 |

### 測試先於實作

單元測試寫在 `concentration` 不存在時，先對一個回傳空值的 stub 跑：9 條全紅。純函式以舊系統 `stock_db` 的真實輸出
為 fixture：2330、8069、6781，2020-01-03 到 2022-06-30，含舊系統 2021-11-26 → 2021-12-24 的缺口（週變化跨過缺口），
以及 6781 的第一個快照（週變化為 NULL），311 週逐位相同。期望值直接取 fixture 的欄位，不經過被測模組的 `METRICS`：
第一版從 `METRICS` 取，stub 的空 `METRICS` 讓它空洞地通過，所以改掉。

整合測試寫在表與資料集都不存在時（import 失敗）。兩條鎖的測試在資料集加進去之後、改用 `backfill.JOBS` 之前單獨
跑過，都是紅的：`derived_store` 只取 `exchange_daily.JOBS` 的鎖，TDCC 的寫入者不在裡面，run 不會等它。其餘行為測試
第一次跑就綠，所以逐一把實作改壞：

| 改壞的方式 | 失敗的測試數 |
|---|---:|
| 只從重寫起點讀快照（看不到前一週） | 3 |
| 輸出從起點的下一週開始 | 3 |
| 序列的 source 對錯 | 5 |
| 用下一週的快照算這一週 | 1（no-future） |

全套測試（從零 migrate 的測試資料庫）：767 passed。`alembic check`（測試資料庫、`stockdc_backfill`）無差異。
ruff：新檔案與改動的檔案 0 個問題。

## `stockdc_backfill` 上的實測

```text
alembic upgrade head                                   0f6386ea08db -> c3f1f515e3df
derived_store --dataset shareholding_concentration     1946 series, 692,660 rows, 41 秒, RSS 62 MB
（再跑一次）                                             0 series, 0 rows
verify_derived_store.py --dataset shareholding_concentration   227 秒，exit 0
verify_derived_store.py --limit 30（其他三個資料集，改動後回歸）   exit 0
```

表大小 120 MB。比率 0 筆 NULL；週變化 NULL 的 1,946 筆恰好是每條序列的第一個快照。

### 舊系統對帳

```text
scripts/reconcile_shareholding_concentration.py --start 2020-01-02 --end 2026-09-11
```

| | 數量 |
|---|---:|
| 今天清單上的股票 | 1,947 |
| 共同的 (股票, 快照日) | 613,353 |
| 逐列欄位（三個比率、spread、兩個人數）不同 | **0**（3,680,118 次比較） |
| `ours_only`：舊系統的 `shareholding` 也沒有這個快照 | 30,948 |
| `legacy_only`：我們的 `shareholding_distributions` 也沒有這個快照 | 159 |
| 週變化不同：前一個快照不同，以我們的比率對舊系統的前一週算，逐位等於舊系統 | 33,527 |
| 週變化不同：舊系統的序列較晚開始（它的 2020-01-03 沒有前一週，我們有 2019-12-27） | 7,080 |
| 週變化不同：舊系統的前一週是只有它才有的快照，舊系統的值等於它自己兩週比率的差 | 476 |
| 週變化不同：舊系統增量執行時看不到前一週（見下） | 8 |
| 無法歸因 | 0 |

- **逐列的值完全相同。** 24-b 已證明兩邊共同的 (週, 證券, 級距 1–15) 輸入 0 差異，這裡的比率與人數也 0 差異。
- **單邊的 key 都是輸入差異。** `ours_only` 有 14,354 筆落在舊系統沒有的 8 週（2020-09-26、2021-02-09、2021-02-20、
  2021-12-03／10／17／30、2023-02-04，24-b 已列），其餘是舊系統過濾掉的股票（24-b 的 868 支）。`legacy_only` 有 129 筆在
  截斷週 2023-10-20（24-b 記載的來源缺陷），30 筆是 7856（見已知限制）。
- **週變化的差異只來自前一個快照。** 一週的輸入不同，下一週的變化就不同。要求的條件比「前一週不同」更嚴：拿我們自己
  的比率，對舊系統認定的前一週算差，要逐位等於舊系統的值。舊系統的前一週我們沒有的（截斷週附近 119 個股票週 × 4 欄），
  改成驗舊系統的值等於它自己兩週比率的差，而這一週本身的比率兩邊相同。
- **舊系統的增量 bug。** 舊系統增量只讀 `date >= 上次處理日`，讓 LAG 看得到上次那一週；某支股票剛好不在上次那一週時，
  LAG 什麼都找不到，變化是 NULL。6241 的 2026-08-28、6461 的 2026-09-11 就是這樣（兩邊的前一週相同，都跳過一週），
  共 2 個股票週 × 4 欄。這裡每次都從第一個快照讀起，所以不會發生。

## 踩到的坑

- **原本以為** `derived_store` 取的鎖涵蓋每一個輸入的寫入者。它只取 `exchange_daily.JOBS`，TDCC 與月營收的 job
  在各自的模組裡。26-b、26-c 的輸入剛好都是交易所日資料所以沒事；集中度的輸入是 TDCC，run 不等它的寫入者，
  `computed_at` 之前蓋章、之後才 commit 的列，下一次 run 永遠看不到。改從 `backfill.JOBS`（所有寫入路徑的 job）取，
  並加一條測試檢查每個資料集的每張輸入表都有寫入者的鎖。
- **原本以為** 每個衍生表的日期欄都叫 `trade_date`。TDCC 的 key 是 `snapshot_date`，有 48 個不是交易日，所以
  `_latest`、`_changed`、`rewrite` 與驗收腳本改成從表的 key 取日期欄。
- **原本以為** fixture 的期望值可以照模組的欄位清單取。stub 的空清單讓對帳測試空洞地通過；期望值要取自 fixture
  本身。

## 已知限制

- 7856 漢測（`stocks.listed_on` 2026-09-22）在 `stockdc_backfill` 沒有 TDCC 歷史，也沒有行情，所以沒有集中度；舊系統
  有它 2026-02-06 起上櫃前的 30 週。TDCC 歷史匯入時它還不在清單上。這是輸入的涵蓋範圍（清單新增的股票要不要補抓
  TDCC 歷史），不屬於 26-d。
- 週變化跨越缺週時量的是兩個快照之間的變化，不是一週：同舊系統，截斷週 2023-10-20 附近與我們沒有的週都是這樣。

## Code review 修正（#57）

| 發現 | 驗證方式 | 處置 |
|---|---|---|
| 對帳腳本在 `wow_previous_snapshot_differs`、`wow_previous_only_legacy_has` 兩個分支對可能是 NULL 的比率呼叫 `Decimal(repr(...))`，遇到 NULL 會丟 `InvalidOperation` 中止，報告不印、結束碼也不是約定的 1 | 執行：`Decimal(repr(None))` 丟 `InvalidOperation`；讀程式：兩個分支都沒擋 NULL | **已修正。** 改成 `_change`：任一邊是 NULL 就回傳 NULL，而走到這兩個分支時舊系統的值一定不是 NULL，所以歸到 `wow_unexplained`。審查舉的例子不成立：截斷週 2023-10-20 被切斷的證券是整列隔離，沒有存成 NULL，`stockdc_backfill` 的比率 0 筆 NULL，這次對帳沒受影響；但 TDCC 寫入端遇到缺級距會寫 NULL，所以是真的潛在中止。重跑對帳，分類與數量和修正前完全相同，exit 0 |

審查確認沒有問題的部分：級距分組、四捨五入與 `-0.0`、週變化的 NULL 傳遞、增量從第一個快照讀起、`period` 同時適用兩種 key、`writer_keys` 涵蓋 TDCC 的寫入者、import 沒有循環、migration 與表定義一致。

## 延後

- 26-e、26-f：融資融券與借券指標、估值指標。
