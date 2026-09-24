# Step 35-c-4 驗收報告

狀態：IN REVIEW (#51)

範圍：衍生資料接 schema v2（ADR-0027「衍生資料：0 張表」）。`technical_indicators:v1` 改從 v2
`daily_prices` 即時計算；定義是程式常數，實作版本是計算時的 git commit。26-a（#44）在 v1 上做的版本
SUPERSEDED，只帶進它的計算器與 fixture 測試。

## 交付

- `src/stock_data_center/v2/indicators.py`：26-a 的計算器，一行不改（244 行）
- `src/stock_data_center/v2/derived.py`：定義常數 `TECHNICAL_INDICATORS_V1`、`history`、
  `TechnicalIndicators.compute`／`rolling`、分段（304 行）
- `scripts/reconcile_technical_indicators.py`：改讀 v2，並把每一筆差異的歸因寫進腳本；任何一筆
  歸不了類或有 `legacy_only`，就以退出碼 1 失敗
- 測試：`tests/unit/test_v2_indicators.py`（5，26-a 的 legacy fixture 測試）、
  `tests/integration/test_v2_technical_indicators.py`（14）
- 文件：ROADMAP §17 與 CLAUDE.md §46 改為「預設即時計算」——這段 owner 在 2026-09-23 已決定，
  但修改只在 26-a 的 branch 上，main 一直還寫著實體化；domain inventory 的兩列一併更正

`src/` 新增 548 行，其中 244 行是原封不動的計算器。

## 規則

| 項目 | v2 的做法 |
|---|---|
| 輸入 | 一支股票、一個來源的 `daily_prices`，一次讀進 |
| 可見性 | 與 `exchange_daily.visible` 同一條規則：key 的第一列（settled 或 provisional）在 `exchange_daily_settled@1` 時刻可見，之後的更正在自己的 `recorded_at` |
| `knowledge_as_of` | 只保留當時已記錄（`recorded_at` ≤ K）的列；整條序列共用 |
| `information_as_of` | `rolling` 隨觀察日移動，是 D 自己的 rule 時刻（D+1 03:00 台北）；`compute` 是呼叫者給的 |
| 分段 | 較早交易日的更正在兩個截止點之間變成可見時才切段；沒有更正就只有一段 |
| 轉板 | 每個來源一條序列（§30）；股票有兩個來源時必須指定 |
| lineage | 每一列帶 `input_count`、`input_fingerprint`（依序的 `<trade_date>@<recorded_at>` 的 SHA-256）、`git_commit` |
| 儲存 | 不寫任何東西 |

## 驗收

| 標準 | 結果 | 證據 |
|---|---|---|
| `technical_indicators:v1` 改讀 v2 `daily_prices` 的可見值 | PASS | `test_the_history_sees_what_exchange_daily_visible_sees`：記憶體中的可見列與 SQL 的 `visible` 在 14 個時刻逐一相同，含 provisional、settled 與更正 |
| 定義改為程式常數，不寫入 `derived_dataset_definitions` | PASS | `test_the_definition_is_a_code_constant`；`v2/derived.py` 不寫任何表 |
| 計算器不變 | PASS | `indicators.py` 與 26-a 逐位元組相同；legacy fixture 5 條測試通過 |
| 結果與 v1 的 26-a 相同 | PASS | 1,959 條序列、2,872,469 列，digest 0 條不同（見下方） |
| 滾動序列等於各截止點的單一 context | PASS | `test_rolling_and_on_demand_agree_for_one_context`，更正切段的情境也逐日比對 |
| 與 legacy 的比對重跑一次 | PASS | 0 筆 `legacy_only`，所有差異歸入 A／B／C，0 筆無法歸因（見下方） |

每條測試都看過它紅；之後逐一把實作改壞（永遠一段、不看 `knowledge_as_of`、不用 release rule、settled 列
等 `recorded_at`、更正也在 rule 時刻可見、用最後一個截止點算全部），每一種都至少讓一條測試失敗。

全套測試：1331 passed、3 skipped（需 `RUN_LIVE_SOURCE_TESTS`）。

## 對照組：v1 的 26-a service

寫程式之前，先用 `origin/step-26-a` 的 worktree，在 `stockdc_backfill` 的 v1 表上，對今天名單上每一對
(股票, 來源) 算 2020-01-02 → 2026-09-11 的滾動序列，知識截止點 2026-09-24 12:00 UTC，每條存 SHA-256
digest（逐年另存一份，差異時可定位）。v2 版以同樣的視窗與截止點重算。

| | v1（26-a，v1 表） | v2（本 step） |
|---|---:|---:|
| (股票, 來源) | 1,959 | 1,959 |
| 列 | 2,872,469 | 2,872,469 |
| digest 不同 | — | **0** |
| 每支 p50／p95／max | 0.854／1.018／1.264 秒 | 0.114／0.141／0.210 秒 |
| 合計 | 1,690 秒 | 208 秒 |

v2 的 `daily_prices` 是 35-a／35-b 從 v1 搬過來、雙向 0 差異的同一批資料，計算器相同，所以結果應該逐位元
相同，實測也是。v2 快 7.5 倍：不再經過 `publication_evidence`（2,200 萬列）解析證據。

## Legacy 對帳

```text
scripts/reconcile_technical_indicators.py --start 2020-01-02 --end 2026-09-11 \
    --knowledge-as-of 2026-09-24T12:00:00+00:00
```

兩邊都只看今天名單上的普通股（ADR-0026）。

| | 全市場 | 交易日集合相同的 1,314 支 |
|---|---:|---:|
| 股票 | 1,945 | 1,314 |
| ours（證券日） | 2,872,469 | 1,934,024 |
| legacy | 2,847,691 | 1,934,024 |
| `legacy_only` | **0** | 0 |
| `ours_only` | 24,778 | 0 |
| 價格類（ma、bb）超過 1e-6 | **0** | 0 |

`ours_only` 的 24,778 個證券日全部是三價為 NULL 的官方無成交列，是 legacy 刪掉的那些。

每一筆超過 1e-6 的差異與 null 不一致都歸類：

| 類別 | 全市場 | 相同交易日子集 | 說明 |
|---|---:|---:|---|
| A. legacy 刪掉無成交日 | 4,249,858 | 0 | 視窗內（指數類：在它之後）有 legacy 沒有的日子 |
| B. 2026-03-27 的成交量被凍結 | 321,737 | 282,540 | 1,027 支只有這一天的成交量不同，只影響 vma |
| C. 轉板 | 35,196 | 19,708 | 14 支有兩個來源，每個來源一條序列 |
| 無法歸因 | **0** | **0** | |

與 26-a 的對帳相比，三類與轉板的 14 支相同；數字較小是因為範圍從 2,568 支證券縮小到今天的普通股。

## 踩到的坑

- **原本以為** ROADMAP §17 已經是「即時計算」。owner 在 2026-09-23 決定了，但那個 commit 只在 26-a 的
  branch 上；main 的 §17 與 CLAUDE.md §46 一直還寫著「每個指標實體化一條滾動序列」。這次帶進 main。
- **原本以為** 26-a 的測試可以照搬。v1 的證據有獨立的 `published_at`，更正可以早於原始列記錄；v2 的更正
  一定晚於原始列記錄。照搬的「更正」測試在 2024 年記錄更正、2026 年記錄原始列，於是 2026 那一列變成了
  「更正」。改成種子資料在自己的 rule 時刻記錄，另加一條測試證明 2026 年才回補的列在 2024 年的 rule 時刻
  就可見。
- 26-a 那條「價格晚於自己的截止點才發布，那一天沒有列」的測試在 v2 不存在：v2 裡 key 的第一列一定在自己的
  rule 時刻可見，只有更正會晚。
- 測試夾具又踩一次 `AmbiguousParameter`（同一個 bind 參數用兩次）：35-b-1、35-c-2 之後第三次。

## 已知限制

- **全市場的單日面板要逐支計算**：指數類指標要從第一筆行情暖機。v2 每支 0.11 秒，全市場一天約 3.5 分鐘
  （26-a 是約 8 分鐘）；Step 27 依實際查詢型態決定要不要實體化。
- **轉板的股票沒有連續序列**（§30）；下游若需要，要有明確的跨來源接續政策（ADR）。
- **更正的路徑沒有真實資料驗證過**：`daily_prices` 在 2020–2026 每個 key 只有一列，分段與「更正不往回
  改寫」只有測試在驗證；Step 28 的前向抓取出現第一筆更正後應回來重跑對帳。

## 延後

- 35-d：刪除全部 v1 程式與表（含 `derived_dataset_definitions`、`derived_metric_versions`、
  `derived_computation_runs`）
- 26-b–26-e：其餘衍生資料集沿用 `v2/derived.py` 的形狀
- （已完成）GitHub 上的 #44 已關閉並註明由 #51 取代
