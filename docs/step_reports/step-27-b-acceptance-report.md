# Step 27-b 驗收報告

狀態：IN REVIEW (#61)

範圍：公開 API 的 HTTP 層與十一個觀測資料集。可見性全部交給 27-a 的 `stock_data_center.v2.visibility`，API 只解析、驗證、
呈現。API 說明在 `docs/api.md`。

## 交付

| | 內容 |
|---|---|
| `src/stock_data_center/api/__init__.py` | `create_app`（API key 中介層、`/v1/datasets`、`/v1/datasets/{name}`）、`read_only`、查詢範圍上限 |
| `src/stock_data_center/api/pit.py` | PIT context 解析：兩種模式不能混用、時間點要帶時區、`latest`／`now` 與預設值解析成請求抵達的時刻 |
| `src/stock_data_center/api/datasets.py` | 公開名稱 ↔ 資料表、每個來源從不發布的欄位 `UNSOURCED`（附依據） |
| `src/stock_data_center/api/render.py` | JSON：精確小數寫出它存的位數，不經過 float |
| `src/stock_data_center/api/__main__.py` | `python -m stock_data_center.api`，只聽 127.0.0.1，key 與 URL 只從環境變數來 |
| `pyproject.toml` | `fastapi`、`uvicorn`（CLAUDE.md §2 的固定技術棧；Pydantic 2 隨 FastAPI） |
| scripts | `verify_api.py`（驗收）、`verify_api.sh`（啟動伺服器、跑驗收、一定收掉伺服器） |
| 測試 | `tests/integration/test_api_observed.py`（21）、`tests/unit/test_api_datasets.py`（3）；`test_v2_independent_of_v1.py` 允許 `stock_data_center.api` |
| 文件 | `docs/api.md`（新）；ROADMAP §20 與 Step 27（27-a 標為 MERGED、新增 27-b 小節）；CLAUDE.md 快照；README；27-a 報告標為 MERGED |

`src/` +426 行。沒有新表、沒有 migration。

## 設計

- **名稱**：十一個資料集用自己的名稱（`daily-prices`、`official-valuations`…），不是表名（§55）；`official-valuations` 特別標明是
  來源公布的估值，跟 Step 26-f 算的 `valuation_metrics:v1` 分開（§53）。
- **驗證**（owner 決定）：每個請求要 `X-API-Key`，以 `hmac.compare_digest` 比對；沒設 key 不能啟動。伺服器只聽 127.0.0.1，
  key 與資料庫 URL 只從環境變數來，不上命令列。
- **PIT context**（owner 決定：預設 latest）：沒帶的 market 參數與 `latest`／`now` 都解析成請求抵達的同一個時刻；回應寫明
  `defaulted` 與 `aliases`（§59）。market 與 system 不能混用。時間點沒有時區就 400。
- **不認得的參數一律 400**：`infomation_as_of` 這種打錯字若被忽略，就會默默變成 latest，這是 PIT 的錯，不是便利性問題。
- **每列**：資料欄位、`recorded_at`、`available_at`、provenance（fetch 與原始檔 SHA-256；TWSE 除權息與減資另帶明細頁的 fetch
  與檔案，§27）。不回傳 `fetch_id`、`detail_fetch_id`（放進 provenance）、`published_at`（`available_at` 由它算）、`retracted`
  （撤銷的事件本來就不回傳）。
- **精確小數**：以存的位數寫成 JSON 數字（`1234.50`），不經過 binary float。
- **沒有來源的欄位**（ROADMAP：「被省略，或明確標示為無法取得」）：來源從不發布的欄位，在該來源的列裡省略，並列在回應與
  `/v1/datasets` 的 `unsourced`。清單是程式常數，每一項有依據：全表型指數檔只有收盤與漲跌、`MI_5MINS_HIST` 沒有漲跌
  （§52、audit §4.2）；公司行動各結果檔只發布自己那一類的條款（audit §4.10 的欄位表）。其餘的 null 是來源那一列沒給值
  （例如沒成交的日子沒有價格）。
- **範圍上限**：沒有 `stock_id` 的查詢最多 31 天（全市場一個月約四萬列日行情）；每次最多 200 個 `stock_id`。
- **唯讀**：每次讀取都在 `SET TRANSACTION READ ONLY` 的交易裡。

## 驗收

| 標準 | 結果 | 證據 |
|---|---|---|
| 不暴露資料表 | PASS | `test_datasets_are_named_apart_from_tables`：沒有任何公開名稱等於表名 |
| 每個回應帶 PIT context，預設值解析成明確時間點 | PASS | `test_no_pit_parameter_means_latest_resolved_to_one_instant`、`test_latest_and_now_are_aliases_resolved_before_the_query`、`test_system_pit` |
| 每列帶 provenance | PASS | `test_a_row_carries_its_times_and_provenance_and_no_storage_detail`、`test_a_corporate_action_names_both_raw_files`；真實資料 12 個查詢每列都有原始檔 SHA-256 |
| 沒有來源的欄位省略或明確標示 | PASS | `test_a_column_the_source_never_publishes_is_omitted_and_named`；`stockdc_backfill` 上 `UNSOURCED` 的每一欄在該來源 0 個值（見下方） |
| API 的答案就是可見性層的答案 | PASS | `verify_api.py`：12 個查詢透過真的伺服器，逐列逐欄等於 `visibility.rows` |
| API key | PASS | `test_every_request_needs_the_api_key`、`test_the_app_refuses_to_start_without_a_key` |

### 測試先於實作

測試寫在 `stock_data_center.api` 只是一個 stub 時：21 條全紅（19 條因 `create_app` 未實作而在 fixture 出錯）。第一次實作有 9 條
紅：`fetches.sha256` 存的是 bytes，要轉成 hex。其餘第一次跑就綠，所以逐一把實作改壞：

| 改壞的方式 | 失敗的測試 |
|---|---|
| 不認得的參數照樣接受 | `test_an_unknown_parameter_is_refused_not_ignored` |
| 沒有來源的欄位照樣回傳 | 2 條 |
| 小數經過 float | `test_a_decimal_is_rendered_exactly` |
| 拿掉全市場查詢的範圍上限 | `test_a_whole_market_query_is_bounded` |
| 交易不設唯讀 | `test_reads_are_read_only` |
| 任何 key 都接受 | `test_every_request_needs_the_api_key` |
| 公司行動不帶明細頁的 provenance | `test_a_corporate_action_names_both_raw_files` |

小數的測試原本用 `1234.56`，經過 float 也印成一樣的字，抓不到改壞；改成 `1234.50`（float 會印成 `1234.5`）才抓到。
`test_api_datasets.py` 的三條寫在 registry 之後，也用改壞驗證：拼錯來源、拼錯欄位、少一個資料集都抓得到；「`fetch_id`
被當成欄位」原本抓不到（測試拿 `HIDDEN` 自己比），改成對照明列的名單後抓到。

全套測試：863 passed（main 839，+24）。ruff：新檔案與改動的檔案 0 個問題。

## `stockdc_backfill` 上的實測

```text
DATABASE_URL=.../stockdc_backfill scripts/verify_api.sh        exit 0，結束後沒有殘留的伺服器
```

**沒有來源的欄位**確實沒有值（`UNSOURCED` 的每一欄在該來源的列數 / 有值的列數）：

| 資料集／來源 | 列數 | 有值 |
|---|---:|---:|
| indices / `twse_mi_index`（開高低） | 111,987 | 0 |
| indices / `tpex_index_summary`（開高低） | 70,732 | 0 |
| indices / `twse_mi_5mins_hist`（漲跌） | 1,630 | 0 |
| corporate-actions / `twse_twt49u`、`tpex_exdailyq`（換股、退還股款） | 6,053、4,532 | 0 |
| corporate-actions / `twse_twtauu`、`tpex_revivt`（權值、配股、認購） | 146、107 | 0 |
| corporate-actions / `twse_twtb8u`、`tpex_pvchgrslt`（價格以外） | 9、13 | 0 |

**透過伺服器的查詢**（latest，逐列逐欄等於 `visibility.rows`）：

| 資料集 | 範圍 | 列數 | 秒 | 回應大小 |
|---|---|---:|---:|---:|
| daily-prices | 2026-09-11 全市場 | 1,936 | 0.19 | 1.2 MB |
| daily-prices | 2330，2020-01-02–2026-09-11 | 1,627 | 0.21 | 1.0 MB |
| indices | 2026-09-01–09-11 | 1,080 | 0.11 | 0.5 MB |
| official-valuations | 2026-09-11 全市場 | 1,935 | 0.12 | 0.8 MB |
| institutional-flows | 2026-09-11 全市場 | 1,810 | 0.20 | 1.2 MB |
| institutional-market-flows | 2026-08 | 294 | 0.02 | 0.1 MB |
| foreign-holdings | 2026-09-11 全市場 | 1,945 | 0.14 | 0.9 MB |
| margin-trading | 2026-09-11 全市場 | 1,841 | 0.18 | 1.1 MB |
| securities-lending | 2026-09-11 全市場 | 1,855 | 0.14 | 0.8 MB |
| shareholding-distributions | 2026-09-11 全市場 | 1,946 | 0.40 | 2.6 MB |
| monthly-revenues | 2330，2020-01–2026-08 | 80 | 0.02 | 0.05 MB |
| corporate-actions | 2330，2020–2026 | 26 | 0.01 | 0.02 MB |

## 踩到的坑

- **原本以為** `fetches.sha256` 是 hex 字串。它是 32 bytes 的 `LargeBinary`，回應要轉成 hex。
- **原本以為** FastAPI 會拒絕沒宣告的查詢參數。它會默默忽略，對 PIT 參數來說這就是「打錯字變成 latest」，所以自己檢查。
- **原本以為** 小數的測試值用什麼都行。`1234.56` 經過 float 印出來一樣，要用有尾數 0 的值才分得出來。
- 35-d 的 `test_src_holds_only_what_v2_keeps` 只允許 v2 保留的模組，新的 `stock_data_center.api` 要加進清單。
- `starlette.testclient` 對 httpx 發出 deprecation warning（建議 `httpx2`）；httpx 是 CLAUDE.md §2 的固定技術棧，不換。

## 已知限制

- 沒有分頁：單次回應以範圍上限控制大小（全市場一天約 1–2.6 MB）。
- 延遲、回應大小等可觀測性（§62）是 Step 30。
- API key 要自己放進 `.env`（`STOCKDC_API_KEY=...`）；`verify_api.sh` 每次跑都自己產生一把臨時 key。

## 延後

- 27-c：財報（版本與 facts）、存表的衍生資料（依 `information_as_of` 過濾）、`technical_indicators_pit:v1`、股票清單與交易日曆。
