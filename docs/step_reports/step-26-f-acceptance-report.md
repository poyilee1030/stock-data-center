# Step 26-f 驗收報告

狀態：IN REVIEW (#59)

範圍：`valuation_metrics:v1`，移植舊系統 `calculate_valuation.py`，含 owner 在 2026-09-25 決定的兩項修正，沿用 26-b 的增量執行器。
Step 26 的最後一個子步驟。

## 交付

| | 內容 |
|---|---|
| schema | `valuation_metrics`：`ttm_eps`、`pe_ratio`、`pe_percentile`、`roe`（double，可為 NULL），key `(stock_id, source, trade_date)`，source 是日行情來源，加 `computed_at`；migration `31e69301ca35`，降級不設防 |
| `src/stock_data_center/v2/valuation.py` | 單季值（含 Q4 推導）、TTM、PE、百分位、ROE 的純函式 |
| `src/stock_data_center/v2/derived_store.py` | 資料集定義；`_reports`（每季第一個版本的公開時間 + 最新版本的數字）；`_report_changes`（新財報讓那支股票的價格序列從它公開那天重算）；`StoredDataset.more_changes`、`exclusive_keys` |
| `src/stock_data_center/v2/financial_reports.py` | 寫入者多拿 job 鎖 `financial_reports/mops_t164sb01` 的共享鎖 |
| scripts | `reconcile_valuation_metrics.py`（新）；`verify_derived_store.py` 加上估值 |
| 測試 | `tests/unit/test_v2_valuation.py`（22，含 code review 補的 1 條）；`test_v2_derived_store.py` 加 10 條；`test_schema_v2_baseline.py` 的衍生表清單 |
| 文件 | ROADMAP §20、Step 26（26-e 標為 MERGED、新增 26-f 小節）；CLAUDE.md 快照；`derived_data.md`、`schema.md`、`market_reference.md`、`financial_xbrl.md`、domain inventory（md／json）、README；26-e 報告標為 MERGED |

`src/` +321／−2 行。

## 每一欄的理由

- `ttm_eps`：要找對「那天已公開」的連續四季、Q4 要用減法推導，拿掉它下游得自己重做公開時間對齊。
- `pe_ratio`：ROADMAP 列的計算 PE，也是百分位排名的對象；四捨五入與「TTM 不為正就 NULL」是定義的一部分。
- `pe_percentile`：對整條序列至今所有 PE 排名，拿掉它每次查詢都要讀整段歷史。
- `roe`：要配對近四季淨利與最新季末權益、依報表別選科目。
- **不存**：`close`（日行情的觀測值）、`pe_official`（`valuations.pe_ratio` 的觀測值）。

## 公式與 owner 決定

- **公開時間對齊**（CLAUDE.md §43）：一季財報從它第一個版本的 `published_at` 落在 Asia/Taipei 的那一天起算，數字用最新版本。沒有
  `published_at` 的財報永不使用（§31）。`stockdc_backfill` 的 42,417 份財報都只有一個版本、都有 `published_at`：多數是
  `financial_statements_general@1` 順延到下一個交易日的法定期限（23:59:59），2025Q4 起有 mtime 證明的更早初見時間。
- **TTM EPS**：最新已公開那一季往回連續四季的單季基本 EPS（9750）之和，四季都要已公開。Q1 單季 = Q1 累計；Q2、Q3 用財報的單季值；
  Q4 = 全年 − Q3 累計（同舊系統），沒有 Q3 就沒有 Q4；Q4 從它和 Q3 兩份財報中較晚公開的那天起才算公開（code review 發現：
  否則 Q3 晚於 Q4 才初見時，由次年 Q3 領頭的四季窗口不檢查這份 Q3，會提前用到它。`stockdc_backfill` 的 9,707 對 Q3/Q4
  沒有一對是 Q3 較晚，已存的表不受影響）。
- **PE** = 收盤價 ÷ TTM EPS；TTM 不為正或沒有收盤價時 NULL；四捨五入照舊系統的 pandas `round(2)`（numpy：`rint(x·100)/100`）。
- **PE 百分位**：當天 PE 在這條序列至今所有 PE（含當天、不含 NULL）中的平均名次 ÷ 個數 × 100，照 numpy `round(4)`。
- **ROE**（owner 決定改定義）：近四季歸屬母公司淨利（合併 8610；個體 8200）÷ 最新一季季末歸屬母公司權益（合併 31XX；個體報表
  沒有非控制權益，用 3XXX）× 100，四捨五入遠離零到兩位；權益不為正時 NULL。舊系統是 TTM EPS ÷ 權益總額（含非控制權益）÷
  （股本 ÷ 10）：假設面額 10 元，2025Q4 至少 16 支不是（0.4–5 元），那些股票的 ROE 差 2–25 倍。
- **欄名**（owner 決定）：去掉 `_official`。
- **列**：股票有成交的日子（volume > 0，舊系統 daily_quotes 保留的日子），而且當天有 TTM。
- **增量**：百分位永遠記得起點，所以每次重寫都從序列第一天讀起，逐位相同、沒有容差。一份財報在上次執行之後記錄，就把那支
  股票的每條價格序列從那份財報第一次公開的那天起重算。

## 驗收

| 標準 | 結果 | 證據 |
|---|---|---|
| 與舊系統的表對帳，差異逐一分類 | PASS | 0 筆無法歸因（見下方） |
| 增量與整段重算逐位相同 | PASS | 1,744 條序列 × 6 個起點經 `derived_store.rewrite`，6,464,306 列 0 差異。重算起點由整合測試驗證：新財報、改版財報、新價格、`test_an_incremental_valuation_equals_the_full_one` |
| 沒有用到未來的資料 | PASS | `test_a_valuation_counts_a_report_from_the_day_it_is_public`、`test_a_reports_day_is_its_taipei_date`、`test_a_valuation_uses_no_later_price_or_report`、`test_no_value_changes_with_a_later_day` |
| 每張新表的理由 | PASS | 上方「每一欄的理由」 |

### 測試先於實作

單元測試寫在 `valuation` 不存在時，先對 stub 跑：21 條中 18 條紅、3 條空洞地綠（沒有正面對照），補上對照後 21 條全紅。
fixture 是舊系統 2021-03-31 到 2022-06-30 的真實輸出：2330、8069、1316（151 天 TTM 為負）、2424（49 個交易日沒有收盤價）。
輸入是舊系統的單季 EPS 與法定期限、每日收盤，期望是舊系統的 `ttm_eps_official`（兩位小數後）與 `pe_percentile_official`；
1,204 天逐位相同，包括百分位。

手算的期望值有兩個寫錯（百分位第 6 天、ROE 半位的例子），在 stub 階段發現並改正；整合測試裡改版財報的期望值也算錯過一次
（Q4 = 全年 − Q3 累計，不是直接給的 EPS），同樣在跑之前改正。

整合測試寫在表與資料集都不存在時（import 失敗），第一次跑就綠，所以逐一把實作改壞：

| 改壞的方式 | 失敗的測試 |
|---|---|
| 寫入者不拿共享的 job 鎖 | `test_a_report_writer_takes_the_shared_job_lock` |
| 衍生執行不拿排他鎖 | `test_a_valuation_run_waits_for_a_report_writer` |
| 新財報不觸發重算 | 3 條 |
| 用最新版本（而不是第一個版本）的公開時間 | `test_a_restated_report_recomputes_from_its_first_publication` |
| 只從重寫起點讀價格 | `test_an_incremental_valuation_equals_the_full_one` |
| 用 UTC 日期而不是台北日期 | `test_a_reports_day_is_its_taipei_date` |

最後一項是另外補的測試：18:00 公開的財報在兩個時區是同一天，抓不到這個錯；00:30 公開的才會。`stockdc_backfill` 有 mtime
在台北凌晨的財報（例如 2026-03-04 01:02）。

全套測試：818 passed（main 786，+32，含 code review 補的 1 條；那條在修正前的程式上是紅的）。`alembic check` 無差異。ruff：新檔案與改動的檔案 0 個問題。

## `stockdc_backfill` 上的實測

```text
alembic upgrade head                                 0487c98a0e37 -> 31e69301ca35
derived_store --dataset valuation_metrics            1959 series, 2,101,240 rows, 130 秒, RSS 63 MB
（再跑一次）                                           0 series, 0 rows
verify_derived_store.py --dataset valuation_metrics  532 秒，exit 0
```

表 277 MB，2021-03-31 起（與舊系統同一天：2020Q1–Q4 在 2021-03-31 才全部公開），1,733 支股票。`pe_ratio` 為 NULL 463,862 列
（TTM 不為正或沒有收盤價）；`roe` 0 列 NULL。

### 舊系統對帳

```text
scripts/reconcile_valuation_metrics.py --start 2020-01-02 --end 2026-09-11      1 分 41 秒，exit 0
```

| | 數量 |
|---|---:|
| 共同的 (股票, 日期) | 2,101,236 |
| TTM EPS 相同（舊系統四捨五入到兩位後） | 2,063,457（98.2%） |
| TTM：公開時間不同（用舊系統的日期重算我們的值，逐位等於舊系統） | 36,602 |
| TTM：舊系統加總了範圍外的財報 | 560 |
| TTM：舊系統缺一季、依列滾動跨了五季 | 617 |
| 百分位不同：兩邊的 PE 歷史不同 | 626,519 |
| 百分位不同：股票轉市場，我們的序列重新起算 | 5,882 |
| `legacy_only`：舊系統加總了範圍外的財報 | 53,281 |
| `legacy_only`：舊系統缺一季、依列滾動 | 3,225 |
| `legacy_only`：公開時間不同 | 54 |
| `ours_only`：舊系統那天沒有報價（2026-03-27，只有零股成交） | 4 |
| 無法歸因 | 0 |

- **公開時間**：我們的 Q2、Q3 比舊系統晚一天（08-15、11-15 對 08-14、11-14），法定期限遇假日順延；2025Q4 起有 mtime 證明的
  財報比舊系統的 03-31 早好幾週。這幾天 TTM 用的季不同。每一筆都要求「用舊系統的日期重算我們的值」逐位等於舊系統。
- **範圍外的財報**：舊系統的 `quarterly_reports_xbrl` 收了股票上市前（興櫃、公開發行）的財報，它自己的 `market` 欄寫著
  `emerging stock market`、`public company` 等；Step 23 只收上市櫃。例：1294 2024-09-26 上櫃，舊系統那天的 TTM 加總了 2020Q4、
  2021Q4、2022Q4、2023Q4 四份年報的 Q4——依列滾動把四年當成四季。也涵蓋舊系統的 Q4 減了一份範圍外的 Q3 的情況（6720 的
  2024Q3 是興櫃時期的財報，所以我們沒有 2024Q4 單季）。
- **缺季**：舊系統沒有 1519 2021Q2、6243 2023Q3 等（23-c 已記載），它依列滾動，TTM 跨了五季；我們要求連續四季。
- **百分位**：一個早期的 PE 不同，之後每一天的排名都跟著變，所以 30% 的百分位不同。每一筆都要求兩件事：兩邊至今的 PE 歷史
  確實不同，而且用我們的演算法對舊系統自己的 PE 歷史排名，逐位等於舊系統的值。1752 等轉市場的股票，我們依來源分成兩條序列
  （§30），百分位在新市場重新起算；舊系統依代號一路算下去。
- **ROE 已改定義，不逐位比對**：2,101,236 筆中 118,070 筆與舊系統相同（5.6%），差距中位數 0.15 個百分點，1,697,438 筆（80.8%）
  在 1 個百分點內。其餘來自面額、非控制權益，以及舊系統的 EPS 用加權平均股數、每股淨值卻用期末股本的差別。

## 踩到的坑

- **原本以為** 財報寫入者和交易所資料一樣，一個 job 一把鎖。它只拿自己那支股票的鎖，衍生執行沒有一把鎖能等「所有財報寫入者」。
  改成寫入者多拿 job 鎖的共享鎖（不同股票的寫入者可以並行），衍生執行拿排他鎖。
- **原本以為** `published_at` 轉日期不用管時區。mtime 證明的初見時間有些在台北凌晨，換成 UTC 會早一天。
- **原本以為** 對帳可以把一支股票的所有序列當成一條。轉市場的股票我們有兩條序列，百分位各自起算；把 PE 歷史混在一起比，
  665 筆被誤判成無法歸因。
- **原本以為** 舊系統的缺值只是缺季。它的 `quarterly_reports_xbrl` 收了上市前的財報，只有年報的年份被依列滾動當成四季。這一類
  占了舊系統獨有列的 94%。
- **原本以為** Q4 的公開時間就是 Q4 財報自己的。它的單季值用了 Q3 的累計，所以也要等 Q3 公開；增量重算從改變的財報第一次公開
  那天起算，這樣 Q3 的改版也不會漏掉 Q4 受影響的日子。
- 26-e 的欄位層級盤點（`data_domain_inventory.json`）還指著舊欄名與不存的複製欄，這次一併改正。

## 已知限制

- 上市前的季度我們沒有財報（Step 23 的 v1 範圍），所以新上市股票要到上市後第四季公開才有估值；舊系統用了興櫃時期的財報。
- 最新值、沒有知識時間軸（§43）：改版財報的數字從該季第一次公開那天就生效。`stockdc_backfill` 沒有任何改版財報，歷史上等於 PIT。
- 財報寫入端的鎖改動讓 Step 23 的寫入路徑多拿一把共享鎖；不同股票的寫入者仍可並行。

## 延後

- Step 26 完成。下一步依 ROADMAP：Step 27 公開 REST API v1、Step 28 排程的前向抓取。
