# Step 36 驗收報告

狀態：IN REVIEW (#67)

範圍：還原價格。`adjusted_prices_pit:v1` 以交易所結果資料的參考價比率（ROADMAP §18、CLAUDE.md §80），在查詢時依呼叫者的
PIT 情境計算往回累積的還原因子，不建表；API 以 `adjusted-prices-pit` 提供。原始 OHLC 不動。

## 交付

| | 內容 |
|---|---|
| `src/stock_data_center/v2/adjusted_prices.py`（新） | 定義常數 `ADJUSTED_PRICES_PIT_V1`、事件來源對價格來源的對照 `PRICE_SOURCE`、純函式 `adjust`、`AdjustedPrices.compute` |
| `src/stock_data_center/api/__init__.py`、`api/derived.py`、`api/openapi.py` | `adjusted-prices-pit`：列在 `/v1/datasets`（`kind: derived_on_demand`），一次一檔，market 與 system PIT 都可 |
| `scripts/report_adjusted_price_gaps.py`（新） | 對 `stockdc_backfill` 的每條序列，把原始與還原收盤價超過漲跌幅的缺口全部分類；有無法解釋的或不連續的事件日就 exit 1 |
| `third-party/fubon/fetch_adjusted.py`、`compare_adjusted.py`（新） | Fubon 還原 K 線的交叉比對（§30.1，只作佐證） |
| 測試 | `tests/unit/test_v2_adjusted_prices_math.py`（13）、`tests/integration/test_v2_adjusted_prices_pit.py`（10）、`tests/integration/test_api_adjusted_prices.py`（4）；`tests/unit/test_api_openapi.py` 跟著列入新資料集 |
| 文件 | `docs/derived_data.md`、`docs/pit_semantics.md`、`docs/api.md`、`docs/data_domain_inventory.md`、audit §4.10、ROADMAP（Step 36、§20、Step 38 狀態、順序）、CLAUDE.md 快照與 §43 |

`src/` +288／−4 行（`adjusted_prices.py` 204 行為新檔）。沒有 migration：不建表。

## 設計

- **不建表**：§43 要求還原價格保留 PIT 的公司行動可見性；Step 26 的表跟著最新輸入覆寫，做不到。少了表不會失去任何東西：
  單檔 2020–2026 整段序列一次讀價格加幾筆事件，實測 2330（1,627 天、26 個事件）0.029 秒、6488 0.027 秒、1563 0.016 秒。
- **因子**：每個事件 `factor(D) = reference_price / close_before`，以 `Decimal` 精確計算並在 `events` 原樣回傳；
  日期 t 的累積因子是 t < D ≤ 錨點的事件因子乘積（浮點），還原開高低收 = 原始 × 累積因子。
- **錨點**：PIT 情境看得到的最後一筆價格，與請求的 `start`、`end` 無關。所以同一天的值不隨查詢區間改變；
  除權息日的價格公布前（`exchange_daily_settled@1`，隔天 03:00），即使事件已在 00:00 可見也不生效。
- **PIT**：價格與事件都經 `visibility.rows` 讀取——可見性唯一的來源（§19）。撤回、更正、`knowledge_as_of`、`system_as_of` 都是那裡的規則。
- **來源隔離**（§30）：TWSE 三個結果資料只調整 `twse_mi_index`，TPEx 三個只調整 `tpex_otc_quotes`；有兩個價格來源的股票必須指定 `source`。
- **因子未知**：事件缺參考價或前收盤價時，它之前的累積因子都是 NULL，不默默略過。現有 10,860 筆事件都有兩個價格。
- **成交量不還原**；純價格序列、2020 年以前、指標的還原版本不在範圍。

## 驗收

| 標準 | 結果 | 證據 |
|---|---|---|
| 每個超過有記載門檻的收盤到收盤缺口（2020 年起）都分類；無法解釋的清單經過審閱 | PASS | 原始序列 833 筆：公司行動 755、新掛牌前五日 76、序列起點 2（創新板掛牌，audit §4.11 有記載）、無法解釋 0。見下 |
| 事件日的連續性 | PASS | 755 筆由事件造成的原始缺口，還原後全部在漲跌幅內；還原序列在事件日超過門檻的 10 筆都是現金增資除權，收盤價落在交易所公布的漲跌停價內，已分類。`close_before` 等於前一筆成交收盤價 10,679／10,683 筆，另 4 筆逐筆說明 |
| 在請求的 PIT 情境中，事件的證據可見之前，任何事件都不會影響序列 | PASS | 永久測試 6 條（見下）；真實資料抽查 2330 2024-06-13 除息 |
| 原始 OHLC 不變、原始與還原可區分（§85） | PASS | 沒有寫入任何表；API 每列同時給 `close_price` 與 `adjusted_close_price`、`adjustment_factor`；`test_an_event_scales_every_earlier_price_by_its_factor` 檢查原始值原封不動 |
| 還原慣例明確、有版本（§85） | PASS | `ADJUSTED_PRICES_PIT_V1` 的 `formula_specification`、`price_adjustment_convention`；`/v1/datasets` 與每個回應的 `derivation` 都帶著，加上 `git_commit`（§42） |
| 舊系統對帳（§78） | N/A | legacy `stock_db` 沒有還原價格：26 張表都沒有任何 adj／factor 欄位（2026-09-26 查），ROADMAP §2.1 也記載舊系統不使用公司行動資料。改以 Fubon 交叉比對佐證 |

### 缺口分類

門檻是漲跌幅限制：相隔 k 個交易日的兩筆成交收盤價，上限 1.1^k − 1、下限 0.9^k − 1（中間沒有成交的日子，參考價仍可逐日移動）。

```text
.venv/bin/python scripts/report_adjusted_price_gaps.py \
    --database-url postgresql+psycopg://stockdc:stockdc@localhost:26519/stockdc_backfill \
    --end 2026-09-11 --output report.json          # exit 0，52 秒
series 1,959   closes 2,845,389

raw      explained_by_corporate_action                          755
raw      explained_by_other_documented_market_event:new_listing   76
raw      explained_by_other_documented_market_event:series_start   2
raw      unexplained_anomaly                                       0
adjusted explained_by_corporate_action (= 不連續的事件日)          0
adjusted explained_by_other_documented_market_event:new_listing   76
adjusted explained_by_other_documented_market_event:series_start   2
adjusted explained_by_other_documented_market_event:within_published_limits 10
event close_before == previous traded close   10,679
event close_before != previous traded close        4
```

- **new_listing**：日期落在該市場掛牌期間（`listings.listed_on`，Step 38-a）的前五個交易日內，交易所那幾天不設漲跌幅。
- **series_start**（審閱）：6423 億而得 2024-05-16（+19.0%）、6794 向榮生技 2024-05-22（+15.7%），都是序列第二個交易日。
  兩檔的 TWSE 期間是創新板（audit §4.11），universe 不收創新板，`listings` 因此沒有那段期間；它們在 `daily_prices` 裡的創新板歷史照舊存在。
- **within_published_limits**：還原序列在事件日跳超過 10% 的 10 筆，全部是現金增資除權（TWSE `權`／`權息` 7 筆、TPEx `除權` 3 筆）：
  1475 2021-04-15（+17.6%）、2243 2023-01-30、2429 2024-07-02（+46.7%）、2464 2026-09-02、2736 2024-05-28、4991 2025-12-22、
  5468 2021-01-27、6224 2021-01-13、6225 2026-08-18（+62.4%）、8033 2023-03-06。每一筆的原始收盤價都在該事件清單檔公布的漲跌停價內
  （腳本從事件列指向的原始檔讀取）。交易所的漲跌停價不以除權參考價為中心：TWSE 兩邊都以不含增資的基準計算，TPEx 漲停以基準、跌停以參考價計算
  （audit §4.10）。市場不反映稀釋時，還原序列移動的就是參考價所模擬的認股權價值——這是 §80 的慣例（認購的股東的報酬），不是算錯。
- **close_before 與前一筆成交收盤價不同的 4 筆**：TPEx 1259 2026-06-25、6103 2025-08-26、6629 2022-07-14、6708 2024-08-30。
  除權息前一天都沒有成交，交易所以當天的最後買賣價當收盤基準（例如 6629 2022-07-13 最後賣價 52.10 = `close_before`）。因子照來源給的算。

驗證「落在公布的漲跌停價內」這件事本身，不只看那 10 筆：全部 TWT49U 與 `exDailyQ` 的列中，有成交的 10,424 個除權息日收盤價都在公布的漲跌停價內
（631 筆有認股比例），161 天沒有成交（audit §4.10）。

### PIT

永久測試（`tests/integration/test_v2_adjusted_prices_pit.py`）：

| 情境 | 測試 |
|---|---|
| 除權息日價格公布前序列不動，公布後事件才生效 | `test_no_event_adjusts_the_series_before_its_ex_date_price_is_public` |
| Data Center 還沒記錄的事件不生效（`knowledge_as_of`、`system_as_of`） | `test_an_event_the_data_center_had_not_recorded_adjusts_nothing` |
| 撤回的事件從撤回列的 `recorded_at` 起不再調整 | `test_a_retracted_event_stops_adjusting_from_its_retraction` |
| 更正的因子從更正列的 `recorded_at` 起生效 | `test_a_corrected_event_changes_the_factor_from_its_recorded_at` |
| 事件只調整自己交易所的價格 | `test_an_event_adjusts_only_the_prices_of_its_own_market` |
| 查詢區間不移動錨點 | `test_the_window_does_not_move_the_anchor` |

真實資料抽查（2330，`stockdc_backfill`）：`information_as_of` = 2024-06-14 02:00 台北時，序列停在 06-12、因子全為 1、沒有事件；
03:00（06-13 價格公布）時 06-13 出現，06-11、06-12 的因子 0.996150 = 905.5／909（除息 3.5 元），`events` 列出該筆。

### 測試先於實作

三個測試檔寫好時 `adjusted_prices` 模組與 API 資料集都不存在，全部在收集或請求時失敗。實作後全綠，再用突變確認守得住：
把事件的讀取改成不看 PIT（永遠用最新情境），撤回、更正、`knowledge_as_of` 三條測試變紅；把錨點改成不設上限，錨點測試變紅。

### 第三方交叉比對（Fubon，§30.1）

`third-party/fubon/fetch_adjusted.py` 抓 11 檔 2024-06-01 至 2026-09-11 的還原日 K（`adjusted=true`），`compare_adjusted.py`
比較雙方都有價格的日子之間的報酬（與錨點無關），容差 0.2%（Fubon 的還原價四捨五入到跳動單位）：

| 事件類型 | 股票 | 結果 |
|---|---|---|
| 現金股利 | 2330（9 次）、1563、3152、4747、7780 | 一致 |
| 除權息（配股） | 5906（TWSE 權息 3 次） | 一致 |
| 減資（彌補虧損、退還股款、現金減資） | 2380、1563、3152、6461 | 一致 |
| 變更面額 | 7780（TWSE）、4747（TPEx） | 一致 |
| 現金增資除權 | 6225、2429、1815（2 次）、6461 | 不一致：Fubon 的報酬等於原始價報酬，也就是對現金增資完全不調整 |

唯一的差異類別是現金增資：我們依 §80 用交易所的除權參考價，Fubon 不調整。依 §30.1，差異分類、不改我們的值。

## 已知限制

- 現金增資除權日，還原序列可能單日移動超過 10%（最大 +62.4%，6225 2026-08-18），是認購股東的報酬；不認購的股東的報酬較接近原始價。
  若要「不反映增資」的版本，需要儲存交易所的 `減除股利參考價`／基準價（audit §6 目前不存），是新的衍生版本，不在 v1。
- 已下市公司沒有價格與事件（Step 38-b），序列存在存活者偏差。
- 一個價格來源一條序列：轉市場的股票（14 檔）兩條序列不接起來。
- 回傳值是浮點：累積因子與還原價以 IEEE 754 雙精度計算，事件因子以 `Decimal` 原樣回傳。

## 測試

```text
.venv/bin/python -m pytest -q tests      986 passed（基線 959，新增 27）
.venv/bin/ruff check <本 step 新增與修改的檔案>   All checks passed
```
