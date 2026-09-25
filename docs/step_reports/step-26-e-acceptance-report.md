# Step 26-e 驗收報告

狀態：MERGED (#58)

範圍：`margin_metrics:v1` 與 `short_interest_metrics:v1`，移植舊系統 `calculate_margin_pressure_analysis.py`、
`calculate_short_interest_analysis.py`，沿用 26-b 的增量執行器。

## 交付

| | 內容 |
|---|---|
| schema | `margin_metrics`：`margin_usage_ratio`、`margin_balance_change_pct`、`short_usage_ratio`、`short_balance_change_pct`、`short_cover_pressure`（double）、`margin_balance_change`、`short_balance_change`（bigint，股）；`short_interest_metrics`：`sbl_balance_change`（bigint，股）、`sbl_balance_change_pct`、`sbl_sell_repay_ratio`（double）。全部可為 NULL，key `(stock_id, source, trade_date)`，加 `computed_at`；migration `0487c98a0e37`，降級不設防 |
| `src/stock_data_center/v2/margin_metrics.py` | 兩個資料集的純函式 |
| `src/stock_data_center/v2/derived_store.py` | 兩個資料集的定義；`_day_rows`：每個值只來自一列輸入的資料集，只讀要重寫的日子 |
| scripts | `reconcile_margin_metrics.py`（新）；`verify_derived_store.py` 加上兩個資料集 |
| 測試 | `tests/unit/test_v2_margin_metrics.py`（12）；`test_v2_derived_store.py` 加 7 條；`test_schema_v2_baseline.py` 的衍生表清單 |
| 文件 | ROADMAP §20、Step 26（26-d 標為 MERGED、新增 26-e 小節）；CLAUDE.md 快照；`derived_data.md`、`schema.md`、`institutional_financing.md`、domain inventory（md／json）、README；26-d 報告標為 MERGED |

`src/` +194／−3 行。

## 每一欄的理由

拿掉這兩張表，資料正確性沒有損失：每個值都是同一列輸入的一次運算。依 Step 26 的決定存表，讓每個衍生資料集走同一條讀取路徑
（owner 在 26-d 做的同一個取捨）。

- 使用率、變化％、券償壓力、賣出／還券比：舊系統下游讀的值，分母為 0 時的 NULL 規則與四捨五入方式是定義的一部分。
- 餘額變化（股）：可以從同一列的餘額與前日餘額算回；照 26-d 的 owner 決定，舊系統有的衍生欄保留。
- **不存**：
  - 綜合壓力分數 `margin_pressure_score`、`short_pressure_score`：ROADMAP 定為下游。
  - 舊系統照抄的餘額、限額、借券賣出／償還：`margin_trading`／`securities_lending` 的觀測值，不重複存（同 26-c 不存發行股數）。
  - 借券表的融券變化與變化％：和 `margin_metrics` 的是同一個值；舊系統兩張表連單位都不同（張與股）。要帶就得配對借券與融資融券
    兩個來源。owner 決定不帶（2026-09-25）。
- 改名（owner 決定，2026-09-25）：舊系統的 `_wow` 是「當天餘額 − 來源公布的前日餘額」，是每日變化，改名 `_change`。

## 公式

- 融資使用率 = 融資餘額／融資限額 × 100；融券使用率 = 融券餘額／融券限額 × 100。
- 融資（融券）餘額變化 = 餘額 − 同一列的前日餘額；變化％ = 變化／前日餘額 × 100。
- 券償壓力 =（融券買進 + 現券償還）／融券前日餘額 × 100。舊系統欄名 `margin_short_cash_repay` 就是融券的現券償還
  （2330 2026-09-10：舊系統 4 張，我們 `short_stock_repayment` 4,000 股）。
- 借券餘額變化 = 餘額 − 同一列的前日餘額；變化％ = 變化／前日餘額 × 100；賣出／還券比 = 借券賣出／還券（不乘 100）。
- 分母不為正時比率為 NULL。比率照舊系統：double 運算、轉 numeric 保留 15 位有效數字、四捨五入遠離零到四位。
- 舊系統融資融券以張計，我們以股計。兩個整數各乘 1,000 後相除，實數上是同一個值，IEEE 除法正確捨入，所以是同一個 double；
  差值的分子也是整數乘 1,000，一樣。對帳 0 差異證實了這點。
- 舊系統的 `COALESCE(x, 0)` 在它自己的資料上從未觸發（兩張輸入表 0 筆 NULL）；這裡來源沒發布的值，讓需要它的欄位為 NULL。
- **沒有暖機**：變化用的是同一列的前日餘額，不讀前一天的列，所以增量執行只讀要重寫的日子。

## 驗收

| 標準 | 結果 | 證據 |
|---|---|---|
| 與舊系統的表對帳，差異逐一分類 | PASS | 0 筆無法歸因（見下方） |
| 增量與整段重算逐位相同 | PASS | `derived_store.rewrite` 在會 rollback 的交易裡，對每條序列 × 6 個起點各執行一次、讀回整條序列：`margin_metrics` 1,855 條序列、6,796,319 列，`short_interest_metrics` 1,883 條、6,905,669 列，全部 0 差異，起點之前的列都沒被動到。重算起點（`_changed`）由整合測試驗證：更正的列、`test_an_incremental_margin_run_equals_the_full_one` |
| 沒有用到未來的資料 | PASS | `test_a_margin_value_uses_no_row_dated_after_it`：加入之後的列、整段重算後，之前的值不變 |
| 每張新表的理由 | PASS | 上方「每一欄的理由」 |

### 測試先於實作

單元測試寫在 `margin_metrics` 不存在時，先對一個回傳空值的 stub 跑：12 條全紅。純函式以舊系統 `stock_db` 的真實輸出為 fixture：
2330、8069、1213 的 2020 上半年，融資融券與借券各 116 天（1213 每天的融資限額都是 0，使用率全為 NULL），舊系統的張數乘 1,000
換成股，逐位相同。期望值取自 fixture 的欄位，不經過被測模組。

整合測試寫在表與資料集都不存在時（import 失敗），第一次跑就綠，所以逐一把實作改壞：

| 改壞的方式 | 失敗的測試數 |
|---|---:|
| 不管重寫起點，讀整條序列 | 2 |
| 讀每天最早而不是最新記錄的列 | 1 |
| 用下一天的列算這一天 | 5 |
| 序列的 source 對錯 | 4 |
| 不經 15 位有效數字直接四捨五入（單元） | 1 |
| 四捨五入改成銀行家捨入（單元） | 1 |

後兩項只有專門的四捨五入測試抓得到：真實 fixture 裡沒有剛好落在半位的值。

全套測試：786 passed（main 767，+19）。`alembic check` 無差異。ruff：新檔案與改動的檔案 0 個問題。

## `stockdc_backfill` 上的實測

```text
alembic upgrade head                                c3f1f515e3df -> 0487c98a0e37
derived_store --dataset margin_metrics              1855 series, 2,656,183 rows, 108 秒, RSS 65 MB
derived_store --dataset short_interest_metrics      1883 series, 2,699,567 rows, 88 秒, RSS 63 MB
（各再跑一次）                                        0 series, 0 rows
verify_derived_store.py（兩個資料集）                  856 秒，exit 0
```

列數等於輸入的 (股票, 來源, 日期) 數：`margin_trading` 1,552,572 + 1,103,611，`securities_lending` 1,558,500 + 1,141,067。
表大小 405 MB、326 MB。

### 舊系統對帳

```text
scripts/reconcile_margin_metrics.py --start 2020-01-02 --end 2026-09-11      3 分 30 秒，exit 0
```

| | `margin_metrics` | `short_interest_metrics` |
|---|---:|---:|
| 共同的 (股票, 市場, 日期) | 2,656,183 | 2,699,555 |
| 比較的（key, 欄位） | 18,593,281 | 8,098,665 |
| 不同 | 0 | 2,318 |
| 其中 `legacy_captured_another_date` | — | 2,318 |
| `ours_only`（舊系統輸入也沒有這一天） | 0 | 12 |
| `legacy_only` | 0 | 0 |
| 無法歸因 | 0 | 0 |

- **融資融券逐位相同。** 21-a 已證明兩邊的輸入逐列相同，張換成股之後，七個指標 1,859 萬次比較 0 差異。
- **借券的差異全在一天。** 2,318 筆都在 sii 2022-10-06。21-b 已證明舊系統那天的 `sii.csv` 其實是 2022-10-18 的資料；這裡要求
  舊系統的值逐位等於我們 2022-10-18 的值，全部成立。
- **12 個只有我們有的 key**：舊系統的 `margin_sbl` 沒有那一列。10 個在 2026-06-30（1623、2072、3485、6983、7772、7795、
  7820、7822、7828、8084），5236 在 2026-07-15（它轉到 TWSE 那天），7743 在 2026-09-09。

## 踩到的坑

- **原本以為** 舊系統的 `_wow` 是週變化，跟 26-d 一樣。它是同一列的「餘額 − 前日餘額」，每日變化；所以不需要讀前一天，也不會有
  26-d 那種前一個快照不同造成的差異。
- **原本以為** 舊系統兩張表的融券變化單位相同。`margin_pressure_analysis` 以張計（−34），`short_interest_analysis` 以股計（−34,000），
  因為它們的輸入表 `margin_trading` 以張、`margin_sbl` 以股。對帳要依表換算。
- **原本以為** 對帳腳本可以像 26-c／26-d 那樣整張表讀進記憶體。舊系統四張表共約 1,100 萬列，而 `margin_trading`／`margin_sbl`
  沒有任何索引，逐股查詢每次都是全表掃描。改成四條依 (股票, 市場, 日期) 排序的串流做 merge join，兩邊都用 `COLLATE "C"` 排序，
  Python 的字串比較才和 SQL 的順序一致。
- **原本以為** 「輸入不同」就足以解釋差異。這太寬：任何輸入差異都會被放過。改成只接受 21-b 已證明的那一天，而且值要逐位等於
  我們在它真正那一天的值，其餘一律無法歸因。
- 26-d 對 `verify_derived_store.py` docstring 的一處修改沒有生效：那次的編輯腳本沒有檢查字串是否對上。這次一併補上，編輯腳本都
  改成先 assert 對上才替換。

## 已知限制

- 綜合壓力分數不在 Data Center：下游要用，自己從這兩張表與觀測值算。
- 變化是對來源公布的前日餘額，不是對我們前一天的列；兩者在來源更正前日餘額時可能不同，但舊系統也是這樣算，這裡照移植。

## 延後

- 26-f：估值指標。
