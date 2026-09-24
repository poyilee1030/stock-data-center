# Step 26-b 驗收報告

狀態：MERGED (#55)

範圍：Step 26 的第一個子步驟。`technical_indicators:v1` 與 `institutional_streaks:v1` 存成寬表，以最新的輸入
增量計算；共用的增量執行器；即時計算的定義改名 `technical_indicators_pit:v1`。Step 26 依 owner 決定（2026-09-24）
依輸入領域拆成 26-b–26-f，編號從 26-b 起，因為 26-a 是已 SUPERSEDED 的 #44。

## 交付

| | 內容 |
|---|---|
| schema | `technical_indicators`（24 個 double 指標欄）、`institutional_streaks`（3 個 int 欄），key `(stock_id, source, trade_date)`，加 `computed_at`；migration `6ecc3eefb103`，不加只新增的 trigger，降級不設防（衍生列都能重算，不是歷史） |
| `src/stock_data_center/v2/derived_store.py` | 增量執行器、兩個資料集的定義與計算、CLI |
| `src/stock_data_center/v2/streaks.py` | 連續買賣超天數的純函式（移植 `calculate_net_streak`） |
| `src/stock_data_center/v2/derived.py` | 即時計算的定義改名 `TECHNICAL_INDICATORS_PIT_V1`／`technical_indicators_pit`；公式文字抽成常數，兩個資料集共用 |
| scripts | `verify_derived_store.py`（存表 vs `_pit`、增量 vs 整段、涵蓋）；`reconcile_institutional_streaks.py`（新）；`reconcile_technical_indicators.py` 改讀存表 |
| 測試 | `tests/unit/test_v2_streaks.py`（8）、`tests/integration/test_v2_derived_store.py`（22）；`test_schema_v2_baseline.py` 加一條；`test_v2_technical_indicators.py` 改名 |
| 文件 | ROADMAP §9、§10（拿掉 seal）、§17、§20、§29、Step 26（拆步表與 26-b）；ADR-0027 修訂段；CLAUDE.md §46、§65 與 step 快照；`derived_data.md`、`pit_semantics.md`、`schema.md`、domain inventory（md／json）、README |

`src/` +425／−18 行（含 code review 修正）。

## 每張表的理由

- `technical_indicators`：拿掉它，全市場單日面板要逐支從第一筆行情暖機，約 3.5 分鐘（35-c-4 實測）；讀存表每支
  0.017 秒。
- `institutional_streaks`：連續天數沒有固定視窗，拿掉它，每次查詢都要從每支股票的法人歷史起點數起。
- `computed_at`：owner 決定保留。它同時是下一次增量的起點：上次 `computed_at` 之後記錄的輸入列，就是要重算的。
  拿掉它就得另存一張執行紀錄表。

## 設計

**一次執行怎麼做**（全在呼叫端的一個交易裡）：

1. 取資料集自己的 advisory lock（兩次執行不重疊），再以共享模式取每個輸入寫入端的 job lock。寫入端持有
   `hashtext(job.key)` 直到 commit，所以這一步會等所有寫到一半的交易結束，並擋住新的寫入直到本次 commit。
   然後以 `clock_timestamp()` 定下 `computed_at`。
2. 每條序列找出上次 `computed_at` 之後記錄的輸入列中最早的日期，從那天起重寫；新的交易日與較早日期的
   更正是同一種情況。
3. 往前讀 500 個日曆日（舊系統的緩衝），且至少涵蓋 240 列（最長的視窗），刪掉那天起的列、寫入新的。

`--full` 從每條序列的第一列重算。要求 READ COMMITTED：REPEATABLE READ 的快照在取鎖之前就定了。

**為什麼要鎖。** `recorded_at` 是 INSERT 語句開始的時間，不是 commit 時間。一個寫入交易的列可能蓋著早於
`computed_at` 的時間，卻在本次讀完之後才 commit；下一次只找 `computed_at` 之後的列，就永遠看不到它。

**`institutional_streaks:v1` 的日子。** 舊系統以 `daily_quotes LEFT JOIN institutional_investors` 計算，
`daily_quotes` 只留有成交的日子。所以這裡的序列是股票有成交（成交量大於 0）的交易日；有成交但沒有法人列的
日子是淨額 0，會中斷連續（交易所的法人檔只列出有法人交易的股票：2020–2026 有 247,326 個有成交的證券日沒有
法人列）；有掛牌但沒成交的日子不算進序列。key 的來源是法人來源：`twse_t86` 用 `twse_mi_index` 的日子，
`tpex_insti_daily_trade` 用 `tpex_otc_quotes` 的日子。

## 決策紀錄

- **暖機緩衝：保留 500 天，驗收改容差**（owner 決定，2026-09-24）。我原本建議照舊系統的 500 天緩衝；實測後
  發現它與「增量＝整段逐位相同」互相矛盾：隨機 60 支、從 2025-06-02 起增量，18,695 列中 `rsi12` 有 5,052 列、
  `macd_dea` 有 7,569 列不逐位相同。我提出三個選項（整條序列暖機、保留 500 天改容差、存指數平均的狀態欄），
  owner 選保留 500 天。容差由下方全市場實測決定。
- **拆步編號從 26-b 起**（owner 決定）：26-a 是 #44，遠端分支 `origin/step-26-a` 也還在，所以這個分支叫
  `step-26-b`，不覆蓋舊分支。

## 驗收

| 標準 | 結果 | 證據 |
|---|---|---|
| 與舊系統的表對帳，差異逐一分類 | PASS | 技術指標：0 筆 `legacy_only`、0 筆無法歸因，與 35-c-4 逐項相同；連續天數：0 筆 `legacy_only`、0 筆無法歸因（見下方） |
| 增量與整段重算：視窗類與計數逐位相同，指數類在容差內 | PASS | 全市場 1,959 條序列 × 6 個起點：技術指標 7,331,288 列 0 筆超出容差、視窗類 0 筆不同；連續天數 7,272,022 列 0 筆不同（見下方） |
| 輸入沒有更正時，整段重算與 `technical_indicators_pit:v1` 逐位相同 | PASS | 1,959 條序列 0 條不同（`stockdc_backfill` 沒有任何被更正的行情 key）；另有 `test_a_full_run_equals_the_pit_reference_bit_for_bit` |
| 沒有用到未來的資料 | PASS | `test_a_value_uses_no_input_dated_after_it`：D 之前的值在加入 D 之後的輸入、整段重算後不變；兩個公式都只沿交易日往前看 |
| 每張新表的理由 | PASS | 上方「每張表的理由」 |

### 測試先於實作

測試在 `derived_store`、`streaks`、新表都不存在時寫好，全部因 import 失敗而紅；`test_schema_v2_baseline` 的新測試
在新表存在之前紅。容差的測試先以 `TOLERANCE = 0.0` 紅，再以實測值填。之後逐一把實作改壞，每一種都至少讓一條
測試失敗：

| 改壞的方式 | 失敗的測試數 |
|---|---:|
| 不取輸入寫入端的鎖 | 2 |
| 不取資料集的鎖 | 1 |
| 永遠整段重算 | 6 |
| 忽略上次的 `computed_at` | 5 |
| 不用緩衝、讀整條序列 | 1 |
| 緩衝不補足 240 列 | 1 |
| `--full` 不先清空 | 2 |
| 連續天數算進沒成交的日子 | 1 |
| 容差永遠通過 | 1 |
| `computed_at` 用 `now()`（交易開始時間） | 4 |
| 淨額 0 不中斷連續 | 8 |

連續天數的純函式另以舊系統 `stock_db` 的真實輸出為 fixture（2330 與 1258 的 2020 年；1258 有 162 個有成交但沒有
法人列的日子），不從自己的實作產生。

全套測試（從零 migrate 的測試資料庫）：730 passed。`alembic check` 無差異（`test_the_metadata_matches_the_baseline`）；
從 baseline 升到 `6ecc3eefb103` 只多這兩張表，降回 baseline 只刪它們（`test_the_derived_tables_are_the_only_ones_after_the_baseline`）。
ruff：新檔案 0 個問題。

## `stockdc_backfill` 上的實測

```text
alembic upgrade head                                  a273160c0288 -> 6ecc3eefb103
derived_store --dataset technical_indicators          1959 series, 2,872,469 rows, 3m54s
derived_store --dataset institutional_streaks         1959 series, 2,847,695 rows, 1m35s
（兩者再跑一次）                                        0 series, 0 rows
```

表大小：`technical_indicators` 768 MB，`institutional_streaks` 326 MB。

### 增量 vs 整段

```text
scripts/verify_derived_store.py        834 秒，exit 0
```

每條序列在 2021-06-01、2022-06-01、2023-06-01、2024-06-03、2025-06-02、2026-09-01 各重啟一次，把增量執行會寫的
列與存表的整段序列比較。

> 26-c 的 code review 發現，這裡的比較只呼叫計算函式，沒有經過 `run` 的刪除與寫入。26-c 把那一步抽成
> `derived_store.rewrite`，驗收腳本改為在會 rollback 的交易裡執行它再讀回；三個資料集重跑的數字與下表相同
> （`docs/step_reports/step-26-c-acceptance-report.md`）。重算起點怎麼選只由整合測試驗證。

| | 結果 |
|---|---|
| 技術指標：比較的列 | 7,331,288 |
| 超出容差 | 0 |
| 視窗類（MA、VMA、Bollinger）不同 | 0 |
| null 不一致 | 0 |
| 存表 vs `_pit` 不同的序列 | 0／1,959 |
| 涵蓋：存表與輸入的日子不同的序列 | 0 |
| 連續天數：比較的列／不同 | 7,272,022／0 |
| 連續天數涵蓋不同的序列 | 0 |

連續天數沒有固定視窗：增量執行只讀 500 天緩衝，若第一個新日期的連續天數從緩衝起點起一直沒斷，它可能早在緩衝之前就開始，
這時該序列改從頭計算，所以增量結果與整段重算完全相等（code review 發現；`test_a_streak_longer_than_the_buffer_is_counted_from_its_start`）。
上表的六個重啟點在 `stockdc_backfill` 上沒有碰到這種情形。

指數類的殘差（都在容差內）：

| 指標 | 有殘差的值 | 最大 | 單位 | 位置 |
|---|---:|---:|---|---|
| `macd_dea` | 1,101,442 | 4.14e-6 | 收盤價的倍數 | 5205（tpex）2022-06-01，起點 2022-06-01 |
| `macd_dif` | 891,107 | 2.90e-6 | 收盤價的倍數 | 同上 |
| `macd_hist` | 1,094,854 | 1.24e-6 | 收盤價的倍數 | 同上 |
| `rsi12` | 734,511 | 6.51e-8 | 絕對值 | 4414（twse）2024-06-21，起點 2024-06-03 |
| `k`、`d` | 118、119 | 3.6e-14 | 絕對值 | 浮點運算順序 |

容差定為 MACD 收盤價的 1e-5、K／D／RSI 絕對值 1e-6，分別是實測最大值的 2.4 倍與 15 倍。`rsi6` 沒有任何殘差。

### 技術指標對帳（讀存表）

```text
scripts/reconcile_technical_indicators.py --start 2020-01-02 --end 2026-09-11
```

| | 本次 | 35-c-4（即時計算） |
|---|---:|---:|
| ours／legacy／shared | 2,872,469／2,847,691／2,847,691 | 相同 |
| `legacy_only` | 0 | 0 |
| `ours_only`（官方無成交列） | 24,778 | 24,778 |
| A. legacy 刪掉無成交日 | 4,249,858 | 4,249,858 |
| B. 2026-03-27 的成交量被凍結 | 321,737 | 321,737 |
| C. 轉板 | 35,196 | 35,196 |
| 無法歸因 | 0 | 0 |
| 價格類（ma、bb）超過 1e-6 | 0 | 0 |
| 每支讀取時間 p50／p95 | 0.017／0.020 秒 | 0.114／0.141 秒（計算） |

### 連續天數對帳

```text
scripts/reconcile_institutional_streaks.py --start 2020-01-02 --end 2026-09-11
```

| | 數量 |
|---|---:|
| ours／legacy | 2,847,695／2,847,691 |
| `legacy_only` | 0 |
| `ours_only` | 4，全是 2026-03-27 |
| 比較的（股票, 日期, 法人） | 8,543,073 |
| 不同 | 25,268（foreign 9,740、dealer 9,217、trust 6,311） |
| B1. 舊系統把另一天的檔案存成這天 | 23,124 |
| B2. 其他法人輸入不同 | 1,935 |
| C. 轉板 | 209 |
| A. 兩邊的日子不同 | 0 |
| 無法歸因 | 0 |

- `ours_only` 的 4 支（1435、2024、2321、6655）在 2026-03-27 只有零股成交：舊系統在零股結算前存下那天的檔案、
  之後沒重抓（35-c-4 的 B 類），成交量是 0，所以沒有那天。
- B1：Step 20-a 的 `legacy_captured_another_date`，六個 TWSE 日期（2021-06-17、2022-04-20、2023-08-04、2024-04-08、
  2024-10-22、2024-10-29）的舊系統檔案屬於別的日期。一天錯的淨額會讓之後整段連續天數都不同，所以列數多。
- B2：抽查 1101 在 2025-02-04、1102 在 2026-01-23，都是 Step 20-a 的 `legacy_row_incomplete`：舊系統的
  `dealer_hedge_net` 是 NULL，其餘值錯位一欄。
- 計數本身沒有差異：A 類是 0，兩邊的輸入相同時連續天數都相同。

## 踩到的坑

- **原本以為** 500 天緩衝足以讓增量與整段逐位相同（上一個 session 的交接也這樣寫）。指數平均的起點誤差每天只
  衰減 25/27（MACD 慢線）或 11/12（RSI12），約 340 個交易日不夠降到最後一位以下。
- **原本以為** 增量的起點用「上次的 `computed_at`」就夠了。`recorded_at` 是語句開始時間，寫入交易 commit 之前
  它的列看不到，卻可能蓋著較早的時間；沒有鎖，下一次增量會永遠漏掉它。
- **原本以為** 連續天數的日子可以直接用法人檔的日子。法人檔只列有法人交易的股票，有 247,326 個有成交的日子
  沒有法人列；舊系統把它們當成淨額 0、中斷連續。
- 測試夾具第四次踩到同一個 bind 參數用兩次的 `AmbiguousParameter`（35-b-1、35-c-2、35-c-4 之後）。

## 已知限制

- **增量的指數類指標有殘差**（owner 接受）：MACD 在收盤價的 1e-5 以內，K、D、RSI 在 1e-6 以內。要逐位等於
  `_pit` 就跑 `--full`。
- **一次執行會擋住輸入的寫入**直到 commit：整段重算約 4 分鐘、每日增量約全市場每支一條 500 天的序列。Step 28
  的排程應在抓取之後再跑衍生。
- **更正的路徑沒有真實資料驗證過**：2020–2026 沒有任何輸入 key 有第二列，只有測試在驗證覆寫；前向抓取出現
  第一筆更正後應回來重跑 `verify_derived_store.py`。

## 延後

- 26-c–26-f：其餘五個衍生資料集，沿用 `derived_store` 的執行器。
- 排程（Step 28）：何時跑增量。
