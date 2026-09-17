# ADR-0022：公司行動 import 路徑——撤回儲存與明細頁前置抓取

狀態：**Accepted**，隨 ROADMAP Step 19-c 合併生效。
落實 ADR-0019 的 `executed_through` 邊界；延伸 ADR-0015 的 raw-first 生命週期框架。

## 背景

Step 19-c 要接上 19-a/19-b 已經寫好的六個 adapter，把已解析的列寫成
`corporate_action_events`/`corporate_action_versions`，並處理兩件框架本來沒有的事：

1. **撤回**：一個 locator 曾經出現、後來在同一個涵蓋範圍內消失，是交易所撤銷了
   該事件，不是我們的資料庫壞了。CLAUDE.md §51.5 要求這是一筆 retraction，
   不是刪除；但現有 schema 只有「業務內容修正」(evidence retraction，§17)，
   從未處理過「業務事件本身被撤回」。
2. **明細頁前置抓取**：TWT49U/TWTAUU 的列本身沒有金額，`observation()` 要有
   `ParsedCorporateActionDetail` 才能完成（19-b）。`RawFirstImporter` 原本
   一次 `run()` 只抓一個 resource，且抓取（HTTP）與寫入（DB transaction）
   刻意分開——`_write_business` 內部發 HTTP 請求會讓交易開著等網路,不能接受。

## 決策

### 1. 撤回是自己的 append-only 表，不是 `corporate_action_versions` 的一列

`corporate_action_retractions(id, event_id, reason, ingested_at,
raw_artifact_id, ingest_run_id)`，`immutable`/`no_truncate` trigger 與其他
Phase 8 表一致（CLAUDE.md §22 偏好 dataset-specific 表勝過鬆散的多型參照）。

`corporate_action_versions` 的每一列都由 `action_type` 決定必要欄位（CHECK
約束見 `7c9e2a4b6d81`），撤回沒有這些欄位可填，硬塞一列等於發明一個新的
「空事件」型別，混淆 CLAUDE.md §24 的 business-content 定義。

唯一鍵是 `(event_id, raw_artifact_id)`：同一份原始位元組重覆匯入,不得產生
第二筆撤回事實（幂等,§76);但**不同**原始位元組各自證明一次「這次看,這個
locator 還是不在」，各留一筆——CLAUDE.md §26「重覆 fetch 的 provenance
仍須可稽核」對業務內容的要求，撤回作為證據性的事實同樣適用，不必收斂成
每個事件一輩子只准一筆。

**目前不解析「重新出現是否等於撤回失效」**：沒有任何 reader 需要這個答案
（沒有 corporate action 的查詢服務存在），先留下事實，等有 consumer 時再決定
解析規則,而不是現在猜一個沒人驗證得了的語意（CLAUDE.md 空的但正確的 contract
不必為了假設中的未來用途先做完）。

### 2. 撤回候選：同一來源、`ex_date` 落在本次涵蓋範圍內的最新版本

```sql
SELECT latest 版本的 event_id, ex_date
  FROM corporate_action_versions
 WHERE source = :source
 -- DISTINCT ON (event_id) ORDER BY ingested_at DESC, id DESC
```

`ex_date` 是六個 feed 共用的執行日（ADR-0019），涵蓋範圍是
`[request.start, min(request.end, request.executed_through)]`——
`ParsedCorporateActionList.coverage_start/coverage_end` 已經算好這個值。
候選集合裡,本次列表沒出現的 event_id 就寫一筆撤回;範圍外的事件從來不是
候選,因此永遠不會被撤回（ROADMAP §19-c 驗收「a row outside the executed
coverage is never retracted by its absence」）。

### 3. `_capture_dependencies`：寫入交易開始前，把 `_write_business` 需要的一切準備好

`RawFirstImporter` 新增一個有預設值（回傳 `None`）的 hook，在主要 resource
`parse()` 成功之後、寫入交易開啟之前呼叫；它的回傳值原樣轉交給
`_write_business` 的新參數 `dependencies`。失敗處理與 `parse()` 完全對稱：
`SourceDataError` 隔離整個 range（保留原始 artifact，不寫業務列），其他例外
記一筆 operational failure——因為明細頁解析不出來,語意上就是同一種
「來源內容對不上契約」失敗，不該落到 writer 錯誤的分類。

配套的 `_capture_and_parse` 重用主要 resource 既有的 checkpoint/resume/raw
store 機制，讓明細頁的抓取一樣是斷點續傳、內容定址、可稽核的。

`CorporateActionImporter._capture_dependencies` 用它把每一列的
`CorporateActionObservation` 在交易開啟前就建好（呼叫 `adapter.observation
(row, detail)`），`_write_business` 因此是純寫入——不再呼叫任何會拋
`SourceDataError` 的解析邏輯。

六個既有 importer 的 `_write_business` 簽章因此多了
`dependencies: object | None = None`，未使用時忽略即可；這是啟用本 PR
範圍內功能所需的共用框架擴充，不是無關重構。

### 4. 六個來源只接受 `capture_bound`，沒有 release rule

`dataset_sources.accepted_evidence_types = ['official', 'capture_bound']`。
結果檔沒有固定發布時刻表——交易所什麼時候執行、什麼時候算完，沒有一個
Step 15 能引用的 statute 或 schedule，因此不宣告 release rule
（對照 18-b 的 `exchange_daily_settled`，那是因為交易日的收盤結算確實有
固定時刻)。第一次看到（`first_capture`/`correction_check`）才能主張
`capture_bound`；其餘情況沿用 pre-ADR-0020 的 `unknown`。

### 5. Downgrade guard 檢查什麼會被 FK 卡住，不是憑空猜

`dataset_sources(dataset_code, source)` 被 `ingest_runs`/`import_manifests`
以真正的外鍵參照（`RESTRICT`）。一度把 guard 縮小成只查
`publication_evidence`，結果 DELETE 本身仍被外鍵擋下，只是從友善的 P0001
變成裸的 23001——這正是 `7a2c9e4d1b58` 自己的註解已經講過的教訓（「Count
what actually blocks the delete」）。guard 因此照抄那個既有模式，查
`ingest_runs`/`import_manifests`/`corporate_action_retractions`。

## 後果

- `test_step19a_corporate_action_terms.py` 的三個 downgrade 測試改為先降到
  `8e4b2c7d9a13`（19-a 自己的修正）再操作，不再途經 `head`——一旦任何
  `ingest_runs` 參照這六個來源，就不可能再降回 19-c 之前，這是
  append-only 史料保護的必然結果，不是這幾個測試的巧合。
- 目前沒有任何 reader 讀 `corporate_action_retractions`；解析「PIT 下這個
  事件現在算不算撤回」是留給下一個需要它的 reader（19-d 回補對帳，或未來的
  corporate-action 查詢服務）的工作，不是本步驟的範圍。

## Step 19-d 追加：年度回補與一個活來源才會暴露的暫時性失敗

### 6. `CorporateActionBackfill`：以年為單位走 2020–2026，不是一次整個範圍

來源可以一次接受整個 2020-2026 的範圍（實測：TWT49U 七年一次請求回傳 OK,
1.5 MB），但回補仍照 ADR-0019「每個 feed 約 7 個請求」以年為單位走,原因與
一次請求能不能成功無關：TWT49U 一年常有上千個事件,每個都要各自的明細頁,
一次七年份、近八千筆明細全部抓完才進第一筆寫入,會讓一次當機的可視進度是
零、也讓單一交易背上七年份的寫入。以年分段,每年有自己的 checkpoint 與
import id（`year_import_id`,仿 `month_import_id`),一年當掉,重跑只補那一年。

### 7. `RetryingFetcher`：一個只有活網路才會暴露的暫時性失敗

2026-09-16 對 TWT49U/TWTAUU 的完整回補實測，TWSE 的 CDN
（`server: HiNetCDN`）偶爾對明細頁請求回應 `307`，內容是
「因為安全性考量，您所執行的頁面無法呈現」的 HTML 安全頁，不是我們的
`SourceDataError`（沒有 `Location`，`httpx` 的 `follow_redirects=True`
因此無從跟隨）。同一個 URL 在幾秒到幾分鐘後重試多半成功；極少數
（實測一筆，TWTAUU 2025 年的一個 `TWTAVUDetail`）在多次重試視窗內持續
擋下，之後才通過——沒有 `Retry-After`，也不是固定的每 N 次請求就觸發。

`RetryingFetcher` 包一層在 `HttpSourceFetcher` 外，對 307/429/5xx 與
timeout 類例外以退避重試（預設 5 次，CLI 用 8 次、封頂 30 秒),用不到就是
把例外原樣丟出，語意不變。只在 `corporate-action` CLI 接上,不是
`RawFirstImporter` 的預設——這是活來源在真正的多千請求量級下才暴露的失敗,
其他資料集的既有回補（17-c、18-b）遇到的是逾時與斷線,不是這種類型;沒有
證據以前，不替它們也換掉預設 fetcher。

`CorporateActionBackfill`/`_capture_dependencies` 兩者當時都保持「一個年度、
一個 range 要嘛全部完成、要嘛整個失敗」的既有顆粒——沒有為了這一個暫時性
失敗新增「單筆事件隔離、其餘照常寫入」的機制。§19-c 已經定的邊界
（一個 range 是一個寫入交易，`SourceDataError` 才隔離)保持不變；一個
retry budget 內解不掉的請求，就是那一年回補失敗、重跑即可，與 17-c/18-b
「一個壞日期回報而不致命」同一個顆粒,不是本步驟該开的新洞。

**這個決定後來被 §8 推翻**——不是因為顆粒選錯，是因為當時沒有實證顯示
「一個壞日期」在 TWT49U 真實資料裡代表什麼：一天可能有二三十家公司同時
除權息，整年失敗、重跑，重跑再失敗在同一個日期，跟整年失敗、只隔離那一天
單筆事件，兩者付出的成本完全不同。

### 8. 真的跑過全歷史才知道：一個壞的明細頁會拖垮同一天其他幾十筆好資料

2026-09-17 對 TWT49U 完整 2020-2026 回補實測（前一版 §7 寫完不到一天）：
`TWT49U` 的「台新戊特二」（2887F，各年不同代碼：2887F/2887Z1/2887G）明細頁
從來沒有資料——不是暫時性,連續數次即時查詢、清掉 checkpoint 逼真的重新
發請求都是同一個回應。§7 原本的判斷（「一個壞日期就是那一年失敗、重跑即
可」）建立在還沒真的撞到這件事的前提上；真的撞到後,對帳腳本抓出 121 筆
legacy `dividend` 有、我們沒有的列，全部落在六個「壞日期」上——因為
TWT49U 的除權息日常常同一天二三十家公司一起除息，整年失敗、只隔離那一天
還是等於那一天全部二三十家的資料都沒進資料庫，不是只有 2887F 那一筆。

這筆資料本身沒有價值（2887F 從來沒有可用的股利明細），但同一天其他公司
的資料是真實、legacy 也收得到的。用手動分段（跳過整個缺口日）繞過的做法
會連帶損失這些資料——這是可以避免的損失，不是無法避免的來源缺口，因此
違反 CLAUDE.md §84 的優先順序（PIT correctness、historical auditability
排在 convenience 之前）。

修法：`_capture_dependencies` 改成逐列處理——一筆明細頁失敗只把**那一列**
標成 `_RowQuarantine`（帶著它自己明細頁的 `run_id`/`raw_artifact_id`，不是
整個 range 主資源的),其餘列正常送進 `_write_business`。`_write_business`
的因應：

- **事件身分照樣為每一列註冊**（`register_corporate_action_events` 吃
  `parsed.rows` 全部,不只成功的列)——一列的明細解不出來,不代表這個事件
  從來源列表裡消失了,§2 的撤回候選邏輯必須繼續把它算作「這次回應仍然
  點名」，否則下一次回補會把它誤判成「來源不再列出」而錯誤撤回。
- **只有解出觀測值的列才寫 `corporate_action_versions`**；解不出來的列
  改成一筆 `import_quarantine`，`resource_key` 指向那一列自己的明細頁
  （不是整個 range），`reason_code` 照舊（`no_data_for_date` 等)。

兩種失敗原因的 checkpoint 處理不同：`invalid_json`（內容根本不是 JSON,
例如網站維護頁)清掉那一列的 checkpoint,下一次用新 import id 的回補
（例如未來的 correction check）會真的重新發請求；`no_data_for_date`
（合法的「查無資料」回應)保留 checkpoint——它是穩定的事實，沒有理由
為了同一個答案再打一次來源。

### 9. PR #28 code review：checkpoint 清掉不等於會被重跑

`/code-review medium` 對 §8 這次改動抓到 4 個問題，3 個是真的：

- **清 checkpoint 沒有用。** §8 原本設計：`invalid_json` 清掉那一列的
  checkpoint,靠「下一次重跑」重新發請求。但逐列容錯之後,主資源多半以
  `succeeded` 收尾,而 `_completed_checkpoint` 只認 `succeeded`
  ——同一個 import_id 的下一次 `run()` 會在最外層直接短路,連
  `_capture_dependencies` 都不會進去,更不會碰到那個被清掉 checkpoint 的
  列。清掉 checkpoint 因此只是理論上「以後可以重來」，實際上除非手動換一
  個新 import_id,不會有任何後續動作去真的重新發請求。修法：
  `_capture_and_parse` 現在在偵測到 `invalid_json` 時**當場**重新發一次
  請求（僅一次)，把原本寄望「以後某次重跑」的自我修復,搬到同一次
  `run()` 裡面真的發生。
- **`_write_business` 的彙總看不到逐列隔離。** `BackfillYearResult`/
  `CorporateActionBackfillReport` 原本沒有 `row_quarantined` 欄位,
  `is_complete` 只看 `failed`——一年裡面有列被隔離,CLI 的彙總跟離開碼
  完全看不出來,只有鑽進那一年自己的 manifest 才查得到,違反 CLAUDE.md
  §78/§79「quarantined records reported」。修法：`_one_year` 讀回
  `manifest.reconciliation["row_quarantined_count"]`,`BackfillYearResult`
  多一個 `row_quarantined` 欄位,`CorporateActionBackfillReport.as_dict()`
  同時輸出彙總數字跟逐年清單——不影響 `is_complete`（隔離是已知、可接受
  的分類,不是失敗),但確保「有隔離」這件事在報告最上層看得到。
- **`RetryingFetcher` 的註解自己說了會接住、其實沒接住。** 模組頂端註解
  一直寫「no usable `Location`, or a loop back to the same URL」兩種情境
  都會重試,但第二種（`Location` 指回同一個網址造成的重導向迴圈）會讓
  httpx 自己先丟出 `httpx.TooManyRedirects`——這個例外繼承自
  `RequestError`,跟 `TimeoutException`/`TransportError` 是平行的類別,
  不會被目前的 `except` 接住。加進重試名單即可,實測沒有改變任何既有
  行為。

第四個（`_capture_dependencies` 第一個 `except SourceDataError` 假設
`error.run_id`/`error.artifact_id`/`error.dependency_resource_key` 一定
存在)追過程式碼後不成立——`_capture_and_parse` 目前只有 `adapter.parse()`
會拋 `SourceDataError`,而那一行正是附加這三個屬性的地方；`_captured_
checkpoint`/`_raw_store.read`/`put`/`fetcher.fetch` 都拋別的例外類別,
不會經過這個分支。但這是共用框架方法,換一個 adapter 或以後改了
`resource()` 的例外型別就會在例外處理器裡面再拋一個 `AttributeError`,
把真正的錯誤原因蓋掉——用 `getattr` 讀、缺任何一個就直接重新拋出（等於
整個 range 那個既有的「不知道怎麼分類就不要猜」邊界),便宜且不改變今天
的行為。

### 10. 重抓一次仍是亂碼：整個 range 失敗，但可續跑

§9 的當場重抓只擋得住瞬間錯誤。TWSE 維護通常持續好幾分鐘，立刻重抓多半
還是同一個維護頁；此時若仍逐列隔離，range 照樣以 `succeeded` 收尾，同一個
import_id 之後的重跑全被 `_completed_checkpoint` 短路，那一列永久沒有
version,backfill 還是 `is_complete: true`、離開碼 0。

修法：重抓後仍是 `invalid_json`,`_capture_and_parse` 改丟
`UnusableSourceResponseError`(不是 `SourceDataError`)。它不會被
`_capture_dependencies` 當成逐列隔離，而是走 `_run_locked` 既有的
`dependency_operational_error` 路徑：

- 不寫 quarantine、不寫任何 version;主資源 checkpoint 維持 `captured`,
  manifest 為 `failed`。
- 亂碼那一頁的 checkpoint 已刪除；其他已抓到的明細 checkpoint 保留。
- backfill 該年回報 `failed`/`operational_error`,`is_complete` 為 false,
  CLI 離開碼 1。
- 用同一個 import_id 重跑：清單與已抓明細直接重放，只重新請求沒成功抓到的
  明細，然後正常完成。

`no_data_for_date` 不受影響：它是穩定的來源答案，仍逐列隔離、保留
checkpoint,不影響 `is_complete`。

## 已否決的替代方案

**在 `corporate_action_versions` 上加一個 `retracted_at` 欄位。** 該表是
append-only 且有 `immutable` trigger 擋 UPDATE；撤回本質上是後來才知道的
事實，用 UPDATE 表達違反 §17/§20。

**只保留一筆撤回、以 `(event_id)` 為唯一鍵。** 同一事件「消失→重新出現→
再消失」時,第二次撤回會被 `ON CONFLICT DO NOTHING` 悄悄吃掉,讓已經佚失的
事實看起來仍然成立。

**用一個 Python 私有方法直接重用 `_run_locked` 的每一步。** 私有方法跨類別
呼叫沒有問題，但那樣明細頁抓取仍發生在 `_write_business` 打開的交易之內，
違反 raw-first 生命週期刻意把 fetch 與 write 分開的設計。

**在 `_write_business` 內呼叫 `adapter.observation()` 建構觀測值。** 那樣
`SourceDataError` 會被寫入交易的例外處理器當成 `writer_operational_error`，
而不是 `_quarantine`——语意上這仍是「來源內容對不上契約」，應該隔離而非
記一筆操作失敗。

**擴大 `RetryingFetcher` 去重試 HTTP 200 但內容是忙碌訊息的回應。**
2026-09-17 對 TWT49U 的完整回補實測，`TWT49UDetail`（僅此一個端點,同網域
的 `TWTAVUDetail`、`TWT49U` 列表當時都正常)持續回 `HTTP 200
{"stat":"系統忙碌中，請稍後再試！"}`——不是 `RetryingFetcher` 認得的任何
可重試 HTTP 狀態碼，等了約 20 分鐘後才自行恢復。沒有加成「200 但
`stat` != OK 也重試」這條規則,原因是目前只有一次真實觀測,無法分辨這是
「這個端點偶爾如此,固定重試幾次會過」還是「這個端點掉線了,重試多少次
都一樣」——貿然加重試會讓一次真正的端點故障看起來像是程式掛住,而不是
明確回報並停手。留給下一次真的復現時,用兩次觀測決定退避曲線,而不是
現在猜一個沒有第二個樣本驗證得了的行為。
