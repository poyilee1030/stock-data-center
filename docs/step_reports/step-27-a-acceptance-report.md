# Step 27-a 驗收報告

狀態：MERGED (#60)

範圍：公開 API 的第一步，PIT 可見性層。每張觀測表都能以 market PIT（`information_as_of` + `knowledge_as_of`）或 system PIT
（`system_as_of`）查詢，沒有 HTTP。Step 27 的拆步與 owner 決定（2026-09-25）記在 ROADMAP Step 27。

## 交付

| | 內容 |
|---|---|
| `src/stock_data_center/v2/visibility.py` | `MarketPIT`、`SystemPIT`（只收帶時區的時間點）、`FAMILIES`（十二張觀測表各一個家族）、`rows`、`report_facts` |
| `src/stock_data_center/v2/exchange_daily.py` | `visible` 改成呼叫 `visibility.rows`（沒有知識截止），簽名不變 |
| 測試 | `tests/integration/test_v2_visibility.py`（22） |
| 文件 | ROADMAP §20 與 Step 27（拆步、owner 決定、27-a 小節；26-f 標為 MERGED）；CLAUDE.md 快照與 §19 的位置；`pit_semantics.md`（可見性的位置、公司行動撤銷、知識截止）；26-f 報告標為 MERGED |

`src/` +196／−33 行。沒有新表、沒有 migration。

## 規則

| 家族 | 表 | key 的首列 | 其後的列 |
|---|---|---|---|
| 交易所日資料 | `daily_prices`、`index_prices`、`valuations`、`institutional_flows`、`institutional_market_flows`、`foreign_holdings`、`margin_trading`、`securities_lending` | `exchange_daily_settled@1`（交易日隔天 03:00） | 自己的 `recorded_at` |
| TDCC | `shareholding_distributions` | `tdcc_weekly@1`（快照日之後第一個週日 12:00） | 自己的 `recorded_at` |
| 公司行動 | `corporate_actions` | `corporate_action_ex_date@1`（除權息日 00:00） | 自己的 `recorded_at` |
| 依發行人公開 | `monthly_revenues`、`financial_reports` | 存的 `published_at`；NULL 永不可見 | 自己的 `recorded_at` |

- 規則型家族：規則時刻前記錄的列是暫定值，規則時刻後記錄的第一列是定案值，兩者都從規則時刻起可見，定案值取代暫定值；之後的列是更正。
- `available_at` 是列的屬性，以該 key 的所有列計算；`knowledge_as_of` 只拿掉之後才記錄的列，不改變其他列的可見時間。
- 公司行動：可見的那一列若是撤銷，事件就不在。財報：版本與它自己的 facts 一起（§20）。
- 沒有 `stock_id` 的表（指數、法人市場彙總）不能用股票過濾，要求時直接報錯，不默默忽略。
- 一個 release rule 屬於它的 (dataset, source)（§29）：每個交易所日資料 job 都宣告 `exchange_daily_settled@1`，沒有 job 覆寫，所以每張表只有一個規則。

## 驗收

| 標準 | 結果 | 證據 |
|---|---|---|
| 每張觀測表都有可見性，且只在一個地方 | PASS | `test_every_observed_table_has_one_visibility_family`；`exchange_daily.visible` 改為呼叫它，`test_exchange_daily_visible_is_the_same_rule` 逐列相同 |
| §66 的永久 PIT 回歸類別 | PASS | 未知公開時間、暫定與定案、知識截止、system PIT 晚到的 ingestion、回補的公開時間、更正可見性、財報與 facts 同一版本、來源規則隔離（TDCC 不用交易所規則）、公司行動撤銷，各一條以上 |
| 時間點要帶時區 | PASS | `test_a_pit_context_needs_timezone_aware_instants` |
| 真實資料規模與正確性 | PASS | 見下方 |

### 測試先於實作

測試寫在 `visibility` 不存在時，先對一個回傳空值的 stub 跑：21 條中 20 條紅，1 條（財報依季末過濾）空洞地綠，補上正面對照後
全紅。

第一次實作跑出一條失敗：公司行動撤銷的測試。錯的是測試的期望值：事件在除息日前記錄（暫定），除息日後第一個被記錄的列就是那筆撤銷，
依文件規則它是定案值，從除息日起就生效。所以測試改成事件在除息日後才記錄（這樣撤銷才是更正），並另加一條測試把「除息日前列出、
之後的檔案沒有了」的後果寫死，也補進 `pit_semantics.md`。

逐一把實作改壞：

| 改壞的方式 | 失敗的測試數 |
|---|---:|
| 不套知識截止 | 4 |
| 每一列都用 `published_at`（不只首列） | 2 |
| 沒有定案值邏輯 | 5 |
| TDCC 用交易所規則 | 1 |
| 撤銷的事件照樣回傳 | 2 |
| 財報的日期取季初 | 1 |

全套測試：839 passed（main 817，+22）。ruff：新檔案與改動的檔案 0 個問題。

## `stockdc_backfill` 上的實測

`scripts` 沒有新增：這一步沒有對舊系統的對帳對象（舊系統沒有 PIT）。以 latest（`information_as_of = knowledge_as_of = now`）與
system PIT（`system_as_of = now`）對 2019–2026 全表查詢：

| 表 | key 數 | market PIT 可見 | system PIT 可見 | 全表查詢 |
|---|---:|---:|---:|---:|
| `daily_prices` | 2,872,469 | 2,872,469 | 2,872,469 | 33.7 秒 |
| `index_prices` | 184,349 | 184,349 | 184,349 | 2.8 秒 |
| `valuations` | 2,872,606 | 2,872,606 | 2,872,606 | 25.2 秒 |
| `institutional_flows` | 2,600,961 | 2,600,961 | 2,600,961 | 28.7 秒 |
| `institutional_market_flows` | 22,778 | 22,778 | 22,778 | 0.8 秒 |
| `foreign_holdings` | 2,868,486 | 2,868,486 | 2,868,486 | 25.3 秒 |
| `margin_trading` | 2,656,183 | 2,656,183 | 2,656,183 | 26.2 秒 |
| `securities_lending` | 2,699,567 | 2,699,567 | 2,699,567 | 24.2 秒 |
| `shareholding_distributions` | 692,660 | 692,660 | 692,660 | 12.5 秒 |
| `corporate_actions` | 10,860 | 10,860 | 10,860 | 0.6 秒 |
| `monthly_revenues` | 149,075 | **139,317** | 149,075 | 1.5 秒 |
| `financial_reports` | 42,417 | 42,417 | 42,417 | 0.5 秒 |

- 月營收少的 9,758 個 key，正好是首列 `published_at` 為 NULL 的 key（2020M01–2026M08），§31 的未知公開時間。
- 一般查詢很快：全市場一天 1,936 列 0.04 秒；一支股票 2020–2026 共 1,627 列 0.02 秒。全表查詢是上界，API 不會這樣查。
- 知識截止：2024-07-01 的日行情在 `information_as_of = 2024-07-02 12:00` 時，`knowledge_as_of = now` 看得到 1,808 列，
  `knowledge_as_of = 2026-09-01` 看得到 0 列——整段歷史都是 2026 年 9 月才記錄的。這是誠實的結果。
- 月營收有 116 個 key 有更正列；其餘表目前沒有更正列。

## 踩到的坑

- **原本以為** 撤銷一定是更正。依規則，除息日後第一個被記錄的列就是定案值，所以「除息日前列出、我們下一次看到時已經撤銷」的事件，
  從除息日起就視為不存在。這是規則的後果，不是例外，現在寫進了文件與測試。
- 測試 fixture 在同一個 `sa.text()` 裡重用了 `:s`，psycopg 報 AmbiguousParameter（memory 記過的坑，第五次）。

## 已知限制

- 可見性每次都以 SQL window function 計算整個 key 的所有列；對一般的查詢範圍很快，全表查詢要半分鐘。
- `technical_indicators_pit:v1` 的 Python 端可見性（`derived.History.visible`）仍是它自己的實作，只用在日行情；27-c 會一起檢視。

## 延後

- 27-b：HTTP 層、API key、PIT context 解析、回應格式、觀測資料的端點。
- 27-c：財報、衍生資料、參考資料的端點。
