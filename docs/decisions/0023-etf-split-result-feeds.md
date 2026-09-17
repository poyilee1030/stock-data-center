# ADR-0023：ETF 分割/反分割結果檔——沒有換股率欄位時存什麼

狀態：**Accepted**，隨 ROADMAP Step 19-e 合併生效。
延伸 ADR-0019 的結果檔識別身分；沿用 ADR-0022 的 import 路徑框架。

## 背景

19-a 找到三個 Step 19 六個 feed 都沒收錄的結果檔：TWSE `rwd/zh/split/TWTCAU`
（ETF分割(反分割)恢復買賣參考價格）、TPEx `bulletin/etfSplitRslt` 與
`bulletin/etfRvsRslt`。三者都要先驗證欄位、單位與真實歷史才能承諾任何
storage contract（ROADMAP §2.4/§19-e）。2026-09-17 對三個端點的真實請求
（`docs/source_field_audit.md` 對應章節）發現兩件本 PR 要處理的事：

1. **TWTCAU 沒有明細頁，也沒有換股率欄位**——只有 `分割(反分割)` 方向文字，
   加上跟其他六個 feed 一樣的兩個官方價格。`formula` 說明只證明
   `恢復買賣參考價 = 停止買賣前收盤價 / 分割（反分割）比率`，這是兩個價格的比值，
   不是交易所發布的整數換股數。
2. **TPEx 兩個 feed 在整個 2020-01-01 → 2026-09-11 都是 `totalCount: 0`**——
   從未列出過一筆真實事件，`詳細資料` 內的標籤схema 因此完全沒有真實資料
   可供驗證。

## 決策

### 1. TWTCAU 存 `other`，不把價格比值當換股率

`corporate_action_versions` 的 CHECK 約束（`7c9e2a4b6d81`）要求
`action_type IN ('stock_split','reverse_split')` 時 `old_shares`/`new_shares`
兩者都必須非空（`old_shares <> new_shares` 且方向與 type 一致）。TWTCAU
沒有可以填進這兩欄的來源欄位——把兩個官方價格相除反推一個整數換股比，
等於用 CLAUDE.md §51.2 保留給「對帳證據」的欄位去發明一個「識別身分」等級
的數字，而且價格比值本身還可能因四捨五入或最後成交價與參考價之間的
市場摩擦而不是乾淨的整數（例如 0050 的 188.65/47.16 ≈ 4.0002，不是精確的 4）。

因此 `TWSEETFSplitAdapter` 存 `action_type="other"`，`source_event_type`
保留原始的 `分割`/`反分割` 字樣，`close_before`/`official_reference_price`
照樣存——跟 `TWTB8U`（TWSE 變更面額，同樣沒有換股率欄位）已經定的先例完全
一致，不是本 PR 發明的新分支。`old_shares`/`new_shares` 依 Source-Field
Rule 留 NULL。

### 2. 沒有 `詳細資料` 欄位的 feed，locator 直接鍵在執行日期上

`_TWSEListAdapter._locator()` 假設每一列都有 `詳細資料` 可以解析出
locator——TWTCAU 的欄位清單完全沒有這一欄。新增一個可覆寫的
`_build_locator()` hook（預設呼叫原來的 `_locator()`），TWTCAU 覆寫成
`ExchangeLocator(self.feed, code, (event_date,))`，跟 TPEx 三個 feed
（本來就沒有 `詳細資料`）用同一個模式。`source_event_key` 因此是
`"TWTCAU:<執行日期>"`——注意這個 key 本身不含證券代碼（`ExchangeLocator.
source_event_key` 從來就是 feed+日期，不含 code），真正的識別身分是
`(security_id, source, source_event_key)` 三元組（CLAUDE.md §51.5）,
兩支不同 ETF 可以合法共用同一個 `source_event_key`——2026-09-17 的真實
資料裡 00673R 與 00706L 就都在 114/10/22 恢復交易，驗收的重複掃描因此按
`(security_id, source_event_key)` 分組，不是 `source_event_key` 單獨判斷。

### 3. TPEx 兩個 feed 出現真實列之前，一律隔離

`詳細資料` 儲存格的九欄表頭跟 `pvChgRslt` 一字不差，但裡面的 HTML 標籤
schema（`變更股票面額換股率:` 之類）從未對著一筆真實 ETF 分割/反分割資料
驗證過——TPEx 從未列出過一筆。抄 `pvChgRslt` 的標籤清單去解析，等於在
Source-Field Rule 底下「promise a stored field」卻叫不出這個 feed 自己
被驗證過的官方端點與樣本。`_TPExETFSplitAdapter._row()` 因此對任何一列
（現實中至今沒有）一律拋 `unverified_schema`，寧可隔離也不要用另一個
feed 的假設去解析。真正出現第一筆事件時，需要用那一筆真實回應重新驗證
`詳細資料` 的內容並補上真正的 `_row()` 實作，不是本 PR 該猜的範圍。

### 4. 一個模糊列，回補要能只隔離它自己那一段

TWTCAU 的 115/03/31（00631L, 元大台灣50正2）方向欄位是空字串——每一筆
其他樣本都有方向，這一筆沒有，因此 `unknown_event_type` 隔離。但整個
2020-2026 一次請求時，這一筆的隔離會把同一次回應裡的另外兩筆乾淨事件
（114/10/22 之後的 00674R 反分割與 00685L 分割，都落在 2026 年）一起卡住，
因為 `_write_business` 之前的 `parse()` 是整段一次成功或一次失敗
（ADR-0022 已定的顆粒）。回補因此把 2026 年拆成
`[2026-01-01, 2026-03-31]`（單獨隔離這一筆模糊列，留下審計紀錄）與
`[2026-04-01, 2026-09-11]`（另外兩筆乾淨事件正常寫入）——不是新增「單列
隔離、其餘照常寫入」的框架能力（ADR-0022 已經否決過這條路，理由不變），
只是用既有的「一次 range 一個顆粒」規則,手動選一個不含模糊列的分段。

## 後果

- `RESULT_FEEDS`（`ingestion/models.py`）新增 `TWTCAU`、`etfSplitRslt`、
  `etfRvsRslt`；沒有這一步，`ExchangeLocator.__post_init__` 會對這三個
  feed 一律拒絕（`unknown_feed`）。
- TPEx 兩個 feed 真正列出第一筆事件時，需要重新讀這個 ADR 的第 3 節，
  用那一筆真實回應驗證 `詳細資料` schema，再實作 `_row()`——現在的
  `unverified_schema` 隔離是刻意的空白，不是遺漏。
- 沒有 legacy 對帳基準：legacy `stock_db` 從未收錄 ETF 分割/反分割，
  三個 feed 的驗收只有零重複識別身分與隔離報告，沒有 §78 的 legacy
  reconciliation 這一項。
