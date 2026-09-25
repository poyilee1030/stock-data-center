# Step 27-c 驗收報告

狀態：IN REVIEW (#62)

範圍：公開 API 的財報、七個存表的衍生資料集、`technical_indicators_pit:v1`、股票清單與交易日曆。可見性仍然只在
`stock_data_center.v2.visibility`（CLAUDE.md §19）；API 只解析、驗證、呈現。API 說明在 `docs/api.md`。

## 交付

| | 內容 |
|---|---|
| `src/stock_data_center/v2/visibility.py` | `derived_rows`：存表衍生列的 `available_at` 與依 `information_as_of` 過濾；`report_facts` 可依 statement、account code 縮小並設上限 |
| `src/stock_data_center/v2/derived_store.py` | `Input` 與 `StoredDataset.depends`：每個衍生資料集的一列讀了哪些輸入、哪個來源、整段序列還是當日 |
| `src/stock_data_center/api/derived.py`（新） | 衍生資料集的公開名稱與定義（§42，輸入以公開名稱列出，§55） |
| `src/stock_data_center/api/__init__.py` | `financial-reports`（帶 facts）、衍生資料、`technical-indicators-pit`、`/v1/stocks`、`/v1/trading-days`；參數檢查抽成 `_params` |
| `src/stock_data_center/api/datasets.py` | 加入 `financial-reports`；`id` 是儲存細節 |
| `src/stock_data_center/api/render.py` | NaN／無限大拒絕輸出（JSON 沒有這種數字） |
| `src/stock_data_center/v2/derived.py` | `TechnicalIndicators.git_commit` |
| scripts | `verify_api.py` 加 `reports`、`derived`、`pit_reference`、`reference_data` 四段（27-b 的兩段不變）；`verify_api.sh` 只改註解 |
| 測試 | `tests/integration/test_api_reports_derived.py`（19）；`tests/unit/test_api_datasets.py` +3（其中 1 條取代「財報是 27-c」那條）；`test_api_observed.py` 的名稱清單改成只比觀測資料集 |
| 文件 | `docs/api.md`、`docs/pit_semantics.md`（衍生列的可見性）、ROADMAP（§20、Step 27、新增 27-c 小節、27-b 標為 MERGED）、CLAUDE.md 快照、README、27-b 報告標為 MERGED |

`src/` +512／−50 行。沒有新表、沒有 migration。

## 設計

- **財報**：每個 `(stock_id, report_year, report_quarter)` 回傳 PIT 看到的那個版本，`facts` 是**該版本自己的**（§20）：重編的
  版本少了一個 fact，那個 fact 在新版本裡就不存在。`start`／`end` 篩季末日（可見性層原本就以季末日當 key 的日期）。
  一份財報約 420 個 fact，全市場一季約 75 萬個（約 100 MB），所以一次最多 200,000 個，超過就 400 並提示用
  `stock_id`、`statement` 或 `account_code` 縮小。
- **存表的衍生資料**（owner 決定：只用 `information_as_of` 過濾）：D 那一列要等**它用到的輸入**全部公開。表以最新輸入覆寫，
  所以 D 用到的可能是之後才更正的值：
  - 一般情形就是 D 自己的規則時刻（交易所資料 D+1 03:00；股權分散是 TDCC 的週日 12:00）；
  - 它讀的某個輸入 key 的最新列若是之後才公開的更正，就等到那個時刻。讀整段序列到 D 的（技術指標的指數平均、連續買賣天數、
    累計買賣超、集中度、估值的百分位）等 D 以前任何一筆；只讀當日那列的（融資融券、借券指標）只等 D 自己的。
  - 每個資料集讀哪些輸入宣告在 `StoredDataset.depends`：法人連續天數以 `twse_t86` 為 key 但算的是 `twse_mi_index` 的交易日，
    累計買賣超的比率用 `twse_mi_qfiis` 當日的發行股數，估值讀的財報以第一版的公布日（台北時間）起算。
  - 集中度的變化量其實只讀上一次快照，這裡宣告成整段序列：只會讓列較晚出現，不會較早。
  - `knowledge_as_of` 或 `system_as_of` 早於請求時刻就 400（表答不出過去知道什麼）；`system_as_of=latest` 回傳全部存的列。
  - 回應寫 `"inputs": "latest"`、定義（資料集代碼、derivation version、公式、輸入資料集、日曆與還原慣例）、每列的 `computed_at`。
- **`technical-indicators-pit`**：一次一檔。`view=as_of`（預設）是 `TechnicalIndicators.compute`，`view=rolling` 是
  `rolling`（每個日期以自己的規則時刻計算，不收 `information_as_of`）。system PIT 400。回應帶 `git_commit`、每列的
  `information_as_of`、`input_count`、`input_fingerprint`。
- **參考資料**：`/v1/stocks`（`market`、可重複的 `stock_id`）回傳今天的清單並寫明 ADR-0026 的存活者偏差、不是 PIT；
  `/v1/trading-days` 回傳 TWSE 交易日（fetch 的 source 是 `twse`）。兩者每列帶 provenance，不收 PIT 參數。

## 驗收

| 標準 | 結果 | 證據 |
|---|---|---|
| 財報：版本與它自己的 facts | PASS | `test_a_report_is_served_as_its_visible_version_with_its_own_facts`；真實資料 2330 26 份、12,706 個 fact 逐一等於 `report_facts` |
| 財報：沒有公布時間的不可見（§31） | PASS | `test_a_report_without_proven_publication_is_market_invisible` |
| 衍生資料依 `information_as_of` 過濾，D 等它的輸入 | PASS | `test_a_derived_row_waits_for_its_own_release`、更正三條（整段序列、當日、跨來源）、估值等重編財報、集中度用 TDCC 規則；真實資料 7 個資料集 × 3 個查詢逐列逐欄等於「規則時刻 ≤ information_as_of 的存表列」 |
| 過去的 `knowledge_as_of`／`system_as_of` 拒絕 | PASS | `test_a_derived_table_refuses_a_historical_knowledge_or_system_cutoff` |
| 回應標明最新輸入與 `computed_at` | PASS | 同上與 `test_a_derived_row_waits_for_its_own_release` |
| `technical_indicators_pit:v1` | PASS | `test_the_pit_reference_answers_what_compute_answers`、rolling 一條；真實資料 rolling 與存表**逐位元相等**（2330、6488 各 1,627 天 × 22 個指標 = 35,794 個值，0 個只在容差內） |
| 股票清單與交易日曆 | PASS | 兩條測試；真實資料 1,947 檔、1,629 個交易日全數回傳 |
| 不暴露資料表（§55） | PASS | `test_every_dataset_is_listed_under_a_public_name`；衍生定義的輸入以公開名稱列出 |

### 測試先於實作

測試寫好時 API 還沒有這些端點：新的 22 條有 21 條紅（含 `test_every_observed_family_is_served`，財報還沒上）。沒紅的是
`test_fact_filters_belong_to_financial_reports_only`：27-b「不認得的參數一律 400」已經擋住它。實作後第一次跑全綠，所以逐一
把實作改壞，每一種都至少有一條測試抓到（`scripts` 外的一次性腳本，改完即還原）：

| 改壞的方式 | 失敗的測試 |
|---|---|
| 不看輸入的更正（只用規則時刻） | 4 條更正／重編的測試 |
| 當日輸入當成整段序列 | `test_a_correction_of_a_same_day_input_delays_only_that_day` |
| 法人連續天數用自己的來源找價格 | `test_a_streak_waits_for_a_correction_of_the_price_days_it_counts_over` |
| 不看財報重編／財報以記錄日而非公布日起算 | `test_valuation_waits_for_a_restatement_of_a_report_public_by_then` |
| 不檢查過去的 knowledge／system 截止 | `test_a_derived_table_refuses_a_historical_knowledge_or_system_cutoff` |
| 不依 `information_as_of` 過濾 | 3 條 |
| 集中度用交易所的規則 | `test_concentration_waits_for_the_tdcc_release` |
| 不依 account code／statement 縮小、不檢查 statement 值、不設 fact 上限 | 各 1–2 條 |
| rolling 當成 as_of、PIT 參考接受 system PIT | 各 1 條 |
| 股票清單不依 market 篩、不檢查 market 值 | `test_the_stock_list_is_todays_universe_with_provenance` |
| 累計買賣超少宣告 `foreign-holdings` | `test_every_stored_derived_dataset_is_served_with_the_inputs_it_reads` |
| NaN 照樣輸出、報表 id 外露、fact 經過 float、定義列出表名 | 各 1 條 |
| 每個資料集都接受 `statement`／`account_code` | `test_fact_filters_belong_to_financial_reports_only`（沒紅過的那條，這樣才證明它守得住） |

全套測試：886 passed（main 865，+21）。ruff：新檔案與改動的檔案 0 個問題。

## `stockdc_backfill` 上的實測

```text
DATABASE_URL=.../stockdc_backfill scripts/verify_api.sh        exit 0，failures 0，結束後沒有殘留的伺服器
```

27-b 的 12 個觀測查詢照舊 0 差異（時間與 27-b 報告相同量級）。資料庫裡**沒有任何輸入有更正列**
（`daily_prices`、`institutional_flows`、`foreign_holdings`、`margin_trading`、`securities_lending`、
`shareholding_distributions` 沒有任何 key 超過一列，財報沒有任何重編），所以每一列衍生資料的 `available_at` 都必須是自己
日期的規則時刻，腳本逐列檢查這一點。

**財報**

| 查詢 | 財報 | facts | 秒 | 回應大小 |
|---|---:|---:|---:|---:|
| 2330，2020–2026 全部 facts | 26 | 12,706 | 0.30 | 3.1 MB |
| 全市場 2026Q2，`account_code=9750` | 1,786 | 7,128 | 0.64 | 2.4 MB |

**存表的衍生資料**（列數；秒）

| 資料集 | 全市場最新一天 | 2330 全歷史，latest | 2330 全歷史，2024-07-02 12:00 |
|---|---:|---:|---:|
| technical-indicators | 1,936；1.21 | 1,627；0.18 | 1,092；0.13 |
| institutional-streaks | 1,912；1.96 | 1,627；0.06 | 1,092；0.05 |
| institutional-cumulative-flows | 1,810；1.93 | 1,627；0.07 | 1,092；0.05 |
| shareholding-concentrations | 1,946；0.41 | 349；0.03 | 235；0.02 |
| margin-metrics | 1,841；1.02 | 1,627；0.08 | 1,092；0.06 |
| short-interest-metrics | 1,855；1.00 | 1,627；0.06 | 1,092；0.04 |
| valuation-metrics | 1,699；0.98 | 1,328；0.05 | 793；0.04 |

2024-07-02 12:00 的查詢只回到 2024-07-01（07-01 的規則時刻是 07-02 03:00）。全市場查詢約 1–2 秒，時間花在找「超過一列的
輸入 key」（每張輸入表一次 group by，約 0.8 秒）；指定股票時是毫秒級。

**`technical-indicators-pit` rolling 對存表**：2330（`twse_mi_index`）、6488（`tpex_otc_quotes`）各 1,627 天、35,794 個值全部
相等，0.26 秒。**參考資料**：`/v1/stocks` 1,947 檔（0.05 秒），`/v1/trading-days` 2020-01-02–2026-09-15 共 1,629 天（0.03 秒）。

## 踩到的坑

- **原本以為** 衍生列 D 的 `available_at` 就是 D 的規則時刻。但表以最新輸入覆寫：D 用到的更正若晚於規則時刻才公開，存的
  那個值在那之前不可能有人看過。所以要看輸入 key 的最新列是不是之後才公開的更正。
- **原本以為** 要把整段輸入序列的可見時間都算出來。只有超過一列的 key 可能是更正：只有一列的 key 在自己的規則時刻可見，
  不晚於 D；財報第一版在公布日當天，也不晚於 D 的規則時刻。只查這些 key，全市場查詢從要掃整張表變成一次 group by。
- **原本以為** 全市場查財報只要日期範圍上限就夠。一季 75 萬個 fact，要另外設 fact 上限。
- `verify_api.py` 的 `_same` 把 float 當成「其他型別」轉字串比，第一次跑報了一堆 `7.52 != 7.52`；float 改成直接比。
- 驗證伺服器有沒有殘留時用 `pgrep -f stock_data_center.api` 會找到下這個指令的 shell 自己；改用 `ps -ef | grep "[m] stock_data_center.api"`。

## 已知限制

- 衍生資料在輸入有更正時，讀整段序列的資料集會把 D 之後所有列延到更正公開的時刻（指數平均確實讀整段；移動平均只讀窗口，
  這是偏保守的一邊，不會讓任何列提早出現）。要「當時看得到的值」請用觀測資料或 `technical-indicators-pit`。
- 全市場的衍生資料查詢約 1–2 秒；可觀測性（§62）是 Step 30。
- 沒有分頁（同 27-b）。

## 延後

- 其他衍生資料集的 on-demand PIT 版本：沒有需求前不做（ROADMAP §26）。
- 還原價格是 Step 36。
