# Step 20-c 驗收報告

Status: IN REVIEW (#32)

範圍：Step 20-d 需要的抓取層，分兩部分。

- `SourceResource` 現在能完整描述一次請求：method、body 和 headers，
  並以固定格式序列化。
- `HttpSourceFetcher` 內建每台主機的請求速率控管器，process 內所有送往
  MOPS 的請求共用同一份配額。

Schema 影響：無，沒有 migration。
PIT 影響：無。publication evidence、release rule 和 business identity 都沒有變動。
規模：`src/` 改動 +245/−18 行（`http.py`、`models.py`，以及 `lifecycle.py`
的小幅修改），低於約 800 行的拆分門檻（`CLAUDE.md` §1）。

## 設計決策

1. **resource 包含什麼。** `method`（`GET` 或 `POST`）、`body`（bytes 或
   `None`）和 `headers`。header 名稱轉成小寫、不可重複、並排序，因此送出
   相同請求的兩個 resource 會相等，序列化結果也相同。
   `SourceResource.form_post` 依欄位給定的順序，產生 ASCII 的
   `application/x-www-form-urlencoded` body，這正是 MOPS `t13sa150_otc`
   需要的請求。建構時會拒絕四種無法一致送出的請求：
   - 帶 body 的 `GET`；
   - `GET`、`POST` 以外的 method；
   - 重複的 header；
   - `Host`、`Content-Length` 或 `Transfer-Encoding` header。這些由 httpx
     依 URL 和 body 自行產生，若允許 resource 指定，存下來的請求可能與實際
     送出的不一致。
2. **序列化。** `to_json()` 產生固定格式的 JSON，body 以 base64 保存，非文字
   的 body 也能逐 byte 還原。`from_json()` 遇到不認識的欄位會拒絕，而不是
   默默丟掉。`to_json_object()`／`from_json_object()` 提供同樣的轉換給
   JSONB 使用。之後排程 job 要存的就是這個格式。
3. **Headers。** fetcher 預設仍送 `Accept: application/json` 和它的
   `User-Agent`。resource 的 headers 會加在預設值上，同名時覆蓋預設值。
   一般 GET 送出的內容和以前完全相同。
4. **請求配額。** `mopsov.twse.com.tw` 的間隔是 3 秒，沿用舊 scraper 的
   `FETCH_INTERVAL_SECONDS`：`my_stock_project`
   `scraper/quarterly/fetch_xbrl.py` 在 2026-07-02 被 MOPS 封鎖後定下這個值
   （註解寫著「別縮短」）。控管器對受控管的主機套用兩條規則：
   - 請求不會同時進行；
   - 每個請求都在前一個請求結束後至少間隔這麼久才送出。失敗的請求也算，
     因為主機一樣收到了。

   主機名稱必須完全相符（忽略大小寫和 port），所以 `mops.twse.com.tw`
   和名稱相似的主機都不受控管。其他主機一律不受控管：TWSE 和 TPEx 的
   backfill 保留各自的 `min_interval_seconds`，把它們移到控管器上不屬於
   這個 step。
5. **每個 process 一個控管器。** 沒有另外指定控管器的 `HttpSourceFetcher`
   都使用 `process_governor()`，`RetryingFetcher` 自己建立的 fetcher 也一樣。
   控管器包住的是請求本身，所以重試也要等主機的間隔。
6. **請求內容寫進 provenance。** `raw_artifact_observations.source_uri` 只存
   URL，而 `t13sa150_otc` 每個日期都 POST 到同一個 URL。resource 不是一般
   GET 時，`resource.request_identity()` 會記錄在兩個地方：
   - 抓取它的那次 run 的 `ingest_runs.run_metadata`。每一次抓取都經過
     `_capture_raw`，所以透過 `_capture_and_parse` 以 dependency 抓取的
     resource 也涵蓋在內，不只主 resource（#32 review）；
   - 主 resource 另外記在 manifest 的 `source_scope`，因此也進入設定
     fingerprint。

   一般 GET 的 identity 是 `None`，所以現有 import 的 run metadata、scope
   和 fingerprint 都不變，跑到一半的 backfill 仍可用原本的 `import_id` 續跑。

## 驗收標準

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| POST resource 序列化後還原，內容完全相同 | PASS | `test_a_post_resource_round_trips_through_serialization_unchanged` 同時檢查還原後的 resource 與原本相等，且再次序列化得到相同文字。`test_a_body_that_is_not_text_round_trips_byte_for_byte` 對含 `\x00\xff` 的 big5 body 做同樣檢查。 |
| 兩個 adapter 同時執行也不會超過主機配額 | PASS | `test_two_adapters_running_together_cannot_exceed_the_host_budget`：兩個 fetcher 在兩條 thread 上共用一個控管器，向 MOPS 主機送出 4 個 GET（月營收）和 4 個 POST（外資持股）。測試確認任兩個請求的時間區間都不重疊，且每個間隔都不小於設定值。 |
| process 內每個 MOPS 請求都經過控管器 | PASS | `test_every_default_fetcher_in_the_process_shares_one_governor` 涵蓋預設建立的 fetcher，包括 `RetryingFetcher` 內部的那個。`test_a_retry_goes_through_the_governor_too` 顯示即使重試本身的 backoff 是 0，仍會等滿 3 秒。 |
| POST 的請求內容記錄在 import 的 provenance 裡 | PASS | `test_a_post_resource_records_its_full_request_in_the_manifest`（integration）把 manifest 的 `source_scope.request` 還原成實際送出的 resource，並在該 run 的 metadata 找到同樣內容。`test_a_dependency_post_records_its_request_on_its_own_run` 對與主 GET 一起以 dependency 抓取的 POST 做同樣檢查。`test_a_plain_get_resource_scope_is_unchanged` 確認一般 GET 的 scope 和 run metadata 不變。 |
| fetcher 送出的就是 resource 指定的內容 | PASS | `test_the_fetcher_sends_the_method_body_and_headers_the_resource_names` 和 `test_a_plain_get_is_sent_exactly_as_before`，兩者都透過 `httpx.MockTransport`。 |

**測試先寫，並確認過會失敗。** 實作前，兩個新的 unit test 檔在 import 時
就失敗；integration test 因為缺少 `request` key 而失敗。review 修正新增的
兩個斷言（主 run 與 dependency run）在 `_capture_raw` 寫入之前，都以
`KeyError: 'request'` 失敗。`test_a_plain_get_resource_scope_is_unchanged`
是防止行為改變的測試，而不是要求改變，所以一開始就通過。變異測試：把
fetcher 的 `governor.slot(...)` 換成 `if True:`，同時執行測試和重試測試都會
失敗；還原後恢復通過。

**完整測試套件通過：** 738 passed、3 skipped。skipped 的是需要手動開啟的
live 測試（`RUN_LIVE_SOURCE_TESTS`）。執行前重建了 `stockdc` 測試資料庫。
Ruff 對新增和改動的程式碼沒有回報錯誤；`models.py` 和 `lifecycle.py` 仍有
三項回報（兩項 import 排序、一項未使用的 import），`main` 上也是同樣三項。

## 實際打 MOPS 的檢查（2026-09-19）

透過新的 `HttpSourceFetcher`（使用 process 控管器），把舊 scraper 的表單
（`step=2&years=2026&months=09&days=11&bcode=`）POST 到
`https://mopsov.twse.com.tw/server-java/t13sa150_otc` 一次，接著再送一次
相同請求。

- 回傳 `text/html`，548,126 bytes；ROADMAP 估計每個日期約 550 KB。表格標題
  是 `115/09/11 外資及陸資投資持股統計`，有 20-d 預期的 11 欄，第一列是
  `00411A 主動統一前沿科技`。
- 第二次請求的總耗時（含控管器等待）是 3.13 秒，兩次回傳內容相同。
- 用嚴格的 big5 解碼會出現 11 個替代字元。要採用哪種編碼（例如 big5-hkscs
  或 cp950）交給 20-d 的 parser 決定。

## 已知限制與延後的工作

- **配額只在同一個 process 內有效。** 兩個獨立的 process（例如 cron job 和
  手動 backfill）各有自己的控管器。v1 只跑一個 ingestion process（ROADMAP
  §3.1：不建 queue，也不拆獨立服務）。如果這點改變，配額就要移到共用的
  狀態，例如 PostgreSQL advisory lock。
- **redirect 不會重新受控管。** 被 redirect 到其他主機的請求不會再經過控管。
  目前受控管的來源都沒有 redirect。
- **TWSE／TPEx 的節奏仍由各自的 backfill 控制。** 它們原本的 sleep 不變。
  改用控管器會改變 Step 17–20 backfill 的行為，ROADMAP 也沒有對應的 step。
- **修正一個既有的 ledger 錯誤。** ROADMAP Step 19-d 章節原本寫
  **IN REVIEW**，但 #28 已合併，§20 表格也早已寫 MERGED。現已改為
  **MERGED** (#28)。
