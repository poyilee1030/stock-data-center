# Step 39-c 驗收報告

狀態：IN REVIEW (#PR)

範圍：歷史產業分類的最後一步。分類期間在查詢時推出（`stock_data_center.v2.visibility.industry_periods`），不另建表；
API 新增資料集 `industry-classifications`，`/v1/stocks` 的下市公司帶最後已知產業。設計見 ADR-0030 §3，
需求見 `stock-model-selection` 的 `docs/requests/industry-classifications.md`。

## 交付

| | 內容 |
|---|---|
| `src/stock_data_center/v2/visibility.py` | `industry_periods_of`（一段掛牌期間在一個 PIT 下的分類期間，純函式）、`industry_chain_issues`（變更鏈一致性）、`industry_inputs`（從資料庫取期間、變更與錨點）、`industry_periods` |
| `src/stock_data_center/v2/industry.py` | 2023-03-28 類別公告日、`existence`（類別在某市場的存續期間） |
| `src/stock_data_center/api/__init__.py` | `GET /v1/datasets/industry-classifications`（`date` 或 `start`/`end`、`stock_id`、三個 PIT 參數）；`/v1/datasets` 的描述；`/v1/stocks` 的 `industry_source` |
| `src/stock_data_center/api/openapi.py` | 資料集與參數說明；`start`/`end` 在共用路徑上不再標為必填（本資料集可只帶 `date`，缺的仍由 handler 回 400） |
| `scripts/report_industry_classifications.py`（新） | 經 API（同一行程、唯讀）量需求方的六項檢查、變更鏈一致性、上櫃逐檔對帳 |
| 測試 | `tests/unit/test_v2_industry_periods.py`（19）、`tests/integration/test_api_industry_classifications.py`（11，含參數化共 19）；`test_api_openapi.py`、`test_api_observed.py` 的資料集清單加入本資料集 |
| 文件 | ADR-0030 §3、`docs/api.md`、`docs/data_domain_inventory.md`、ROADMAP、CLAUDE.md 快照、README；39-b 改為 MERGED |

`src/` 新增約 400 行。

## 期間怎麼推出（ADR-0030 §3）

一段掛牌期間（`listings` 的一列）一條鏈，只看該市場交易所的公告：

| `basis` | 類別 | `available_at` |
|---|---|---|
| `before_change` | 第一次變更的原類別 | 期間起日 00:00（當時使用中的類別本來就公開，決定 3） |
| `change` | 變更的新類別 | 發文日隔天 00:00（`industry_announcement_next_day@1`）；更正自其 `recorded_at` |
| `anchor` | 沒有變更時：掛牌中取今天的 ISIN 類別，已結束取最後交易日的類股行情 | 期間起日 00:00 |
| `rename` | 16 在 2023-07-03 改名觀光餐旅 | 2023-03-29 00:00（兩個交易所 2023-03-28 的公告） |

PIT 的三條規則，都有單元測試：

- 鏈只用 `knowledge_as_of`（`system_as_of`）之前記錄的東西；ISIN 類別以存它的那次 fetch 時間為準。之前的查詢是空的。
- 還沒公開的變更不出現，它的實施日也不會變成前一段的 `effective_to`（否則未來的日期會漏出來）；下市日在當天之後才出現。
- 任何一段都不超出其類別的存續期間（35–38 自 2023-07-03；櫃買的 18、34 到 2023-07-03）。

## 驗收

| 標準 | 結果 | 證據 |
|---|---|---|
| `start`/`end` 回傳重疊的期間，`date` 回傳當天有效的期間，`stock_id` 可重複 | PASS | `test_a_range_gives_every_period_overlapping_it`、`test_a_date_gives_the_category_in_effect_that_day`、`test_the_periods_come_from_one_place` |
| 三個 PIT 參數；market PIT 下 `available_at` 不晚於 `information_as_of`，未公開的變更不從結束日漏出 | PASS | `test_market_pit_returns_only_what_was_public`、`test_system_pit_is_answered`、`test_knowledge_before_anything_was_recorded_sees_nothing`；單元 `test_before_its_notice_is_public_…`、`test_a_correction_…`、`test_nothing_recorded_…`；真實資料見檢查 6 |
| 每段期間帶 `basis`、`source`、`recorded_at` 與 provenance | PASS | `test_a_period_names_its_basis_source_and_provenance` |
| 無法回答的請求 400（`date` 與 `start` 同時、都沒有、2020-01-02 之前、不存在的日期、`source`／`market`、超過 200 檔） | PASS | `test_a_request_it_cannot_answer_is_400`（9 組） |
| `/v1/stocks` 的下市公司帶最後已知產業並標明來源 | PASS | `test_a_delisted_company_carries_its_last_known_industry`；真實資料 56 家 NULL 中 55 家補上（5259 沒有期間） |
| 變更鏈一致性零不一致，或每筆有分類 | PASS | 見下：0 筆；2 筆「在期間外」有說明 |
| 需求方的六項檢查 | PASS（第 4 項上市部分無來源，照實揭露） | 見下 |
| 上櫃對帳逐檔一致，或每筆差異有分類 | PASS | 見下：30 筆全屬櫃買 2021 年行情延後套用 |

### 測試先於實作

測試寫好時 `industry_periods_of` 等都不存在：單元測試以 AttributeError 失敗，API 測試因資料集不存在回 404 而失敗。
實作後有兩處是測試寫錯：整合測試的 seed 用一次多列 insert，第一列沒有 `delisted_on`，後面各列的下市日就全被丟掉
（SQLAlchemy 以第一列決定欄位）；改成每列都給齊欄位。另外 `test_api_openapi.py` 原本斷言 `start`/`end` 必填，
這是本步刻意改變的合約（只帶 `date` 也合法），斷言隨之更新；它和 `test_api_observed.py` 的資料集清單也因新資料集而紅，屬預期。

再把實作逐一改壞 17 種方式確認都有測試抓到；第一輪只漏「錨點取第一筆而非最後一筆觀測」（seed 每檔只有一筆觀測），補一筆較早、類別不同的觀測後抓到。

| 改壞的方式 | 失敗的測試 |
|---|---|
| 未公開的變更照樣出現 | `test_a_correction_is_seen_only_from_when_it_was_recorded` |
| 第一段以變更的公開時點為準 | `test_a_change_splits_the_span_and_is_public_from_the_day_after_its_notice` |
| 變更／錨點不看 `knowledge_as_of` | `test_nothing_recorded_by_knowledge_as_of_gives_no_period` |
| 不依類別存續裁切起日／迄日 | `test_a_change_recorded_after_knowledge_as_of_is_not_used`、`test_a_period_ends_no_later_than_its_category_existed` |
| 不切改名 | `test_the_2023_rename_splits_a_tourism_period` |
| 下市日在當天之前就出現 | `test_a_closed_span_ends_at_its_delisting_once_that_has_happened` |
| 未公開的下一段從結束日漏出 | `test_a_stock_never_reclassified_has_one_period_from_its_start` 等 |
| 錨點取第一筆觀測 | `test_a_date_gives_the_category_in_effect_that_day`（補 seed 後） |
| `date` 把結束日當天也算進去 | `test_a_date_gives_the_category_in_effect_that_day` |
| 掛牌中的期間不用 ISIN 錨點 | 同上 |
| 不檢查鏈的銜接／錨點 | `test_a_chain_that_does_not_link_is_reported` |
| `/v1/stocks` 不補最後已知產業 | `test_a_delisted_company_carries_its_last_known_industry` |
| `date` 與 `start` 同時可用、接受 2020-01-02 之前 | `test_a_request_it_cannot_answer_is_400` |

全套測試：1177 passed（另見「範圍外的修正」）。ruff：改動與新增的檔案沒有問題。

## 真實資料（`stockdc_backfill`，經 API 量）

`DATABASE_URL=… python scripts/report_industry_classifications.py > log/step-39-c-report.json`

| | |
|---|---|
| 掛牌期間 | 2,019（其中 163 段有變更） |
| 分類期間 | 2,228：`anchor` 1,857、`before_change` 161、`change` 168、`rename` 42 |
| 來源 | `twse_isin` 1,831、`tpex_announcement` 207、`twse_announcement` 123、`tpex_otc_quotes` 38、`twse_mi_index` 29 |
| 沒有期間 | 5259（2020-01-09 下市，窗內沒有行情，39-b 已報告） |
| 整個市場、整個期間一次查詢 | 0.14 秒、2,228 列、936 KB；`date=` 0.12 秒 |

### 變更鏈一致性

**0 筆不一致**：每次的原類別都接得上前一次、錨點都等於鏈在錨點那天的類別、每段類別都在其存續期間內。

- 第一輪有 1 筆是檢查本身的誤報：4712 的錨點是最後交易日 2024-02-05 的 02 食品工業，之後 2024-06-03 才改為 20 其他，
  2024-09-25 下櫃；原本拿錨點比最後一次變更之後的類別。改成比錨點那天的類別（錨點帶自己的日期），先寫會紅的測試再修。
- 2 筆「在期間外」：6869、6873 在 2023-07-03 被證交所調整時還在創新板（不在範圍），2024 年才轉主板，變更不屬於這段期間。

### 需求方的檢查（`docs/requests/industry-classifications.md` §7）

| # | 檢查 | 結果 |
|---|---|---|
| 1 | 今天屬於四個新類別的股票，2023-07-03 以前有別的分類 | 140 檔（與需求方的數字相同）；96 檔在 2023-07-03 前有期間，全部不是 35–38；44 檔在那之後才掛牌，之前沒有期間 |
| 2 | 2888 在 2025-07-24 前、2867 在 2026-09-01 前是金融保險業 | 2888 在 2025-07-23 是 17 金融保險業、07-24 沒有期間；2867 在 2026-08-31 是 17、09-01 沒有期間 |
| 3 | 2448 在 2020 年有分類 | 2020-01-02 與 2020-12-31 都是 26 光電業（證交所重建的最後已知類別） |
| 4 | `date=2023-06-30` 各類別股票數與成分股數一致 | 上櫃：26 個類別與當天櫃買類股行情的家數相同，只有 22（91 對 90）與 32（24 對 23）各多 1 家——4192、4806 當天沒有行情（不在全市場檔），所以不在任何類別頁。上市：沒有來源給過去某天的類別成分（證交所的類股行情以今天的分類重建），無法驗證 |
| 5 | 目前有效的期間等於 `/v1/stocks` 今天的 `industry` | 1,947 段，0 段不同；每檔掛牌中的股票都有目前有效的期間 |
| 6 | 任一時點 T，`available_at` 不晚於 T | 546 個時點（每個 `available_at` 與其前一秒、2020-01-01、現在）：0 段晚於 T；0 段的結束日在 T 時尚未公開 |

檢查 6 的第一版把 268 段算成「結束日尚未公開」：都是 2023-07-03 類別本身的變動（18、34 併入其他類、16 改名），
自 2023-03-29 起就由類別公告得知，是檢查漏了這條規則，不是外洩；補上後為 0。

### 上櫃對帳

39-b 存的 58 個櫃買類股行情日期，每一檔的行情類別與當天有效的期間比對：

| 結果 | 筆數 |
|---|---|
| 一致 | 46,710 |
| 櫃買 2021 年行情延後套用 | 30 |
| 不同 | 0 |
| 有行情、沒有期間 | 0 |

30 筆是 110 年公告的 10 家公司在 2021-06-01、06-02、06-09 的行情仍是原類別（櫃買到 2021-06-10 才套用，39-b 報告）；
期間照公告的實施日期 2021-06-01（ADR-0030 決定）。

### 與 legacy `stock_db` 對帳（§78）

legacy 只有今天的產業快照，已在 39-a（有變更的 163 檔：160 同、0 異）與 39-b（2026-09-15 上櫃 887 檔：885 同、0 異）對過；
本步的目前有效期間與 `stocks` 今天的 ISIN 類別 1,947 段全部相同（檢查 5），沒有新的比對對象。

## 範圍外的修正

`tests/integration/test_v2_industry_changes.py::test_a_refetched_notice_without_a_company_is_quarantined`（39-a）是 flaky：
假時鐘讓每次抓取的 `fetched_at` 相同，測試用 `ORDER BY fetched_at DESC, id` 取「最新」一筆，`id` 是隨機 UUID，
連跑三次紅兩次。它讓全套測試結果不穩定，擋住 §85 的「測試通過」，所以只改測試：改為斷言那個 resource 的兩次抓取
一次 succeeded、一次 `notice_changed`。實作沒有改。

## 已知限制

- 上市沒有當日分類的來源：公告的完整性只能靠變更鏈一致性佐證，從未出現在公告裡的變更抓不到（ADR-0030 §6）。
- 下市的上市公司，類別是證交所以今天分類重建的最後已知類別。
- `knowledge_as_of` 早於最近一次 ISIN 刷新時，沒有公告過變更的股票沒有期間：`stocks` 是就地刷新的參考資料，舊值沒有保留。
- 5259 沒有期間。
- API 容器要在合併後重新部署（migrate 不需要：本步沒有 migration）。

## 延後的工作

- 前向抓取（Step 28）：每年的調整公告、新下市公司的錨點（`industry_observations --anchors`）。
- 38-b（下市公司的各資料集）之後，下市公司的期間就能配上它們的行情與財報。
