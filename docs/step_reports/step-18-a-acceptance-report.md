# Step 18-a 驗收報告

狀態：IN REVIEW

範圍：市場指數 adapter

Schema 影響：無。Migration 影響：無。PIT 影響：無——這個 step 不寫入任何東西；
它把官方 bytes 轉成 `MarketIndexObservation` 值。
`src/` 改動 +622/−0 行。

## 為什麼 Step 18 拆成三部分

五個 adapter 和兩個 importer 遠超過一個 pull request 能審閱的量（CLAUDE.md §1）。
接縫就是 Step 17 證明過的那些：18-a 解析，18-b 匯入並 backfill，18-c 處理第二個
資料集。指數和估值是不同資料表中的不同資料集，所以每一部分本身都是正確的。

## ROADMAP 要求的 spike，已解決

Step 18 的前提是先對 TPEx 做 spike，找出櫃買指數 OHLC 端點，才能承諾任何東西。
audit 記錄的否定結果**是錯的，而資料仍然無法 backfill**。

`openapi/v1/tpex_index`（`櫃買指數歷史資料`）確實為 `櫃買指數`（TAIEX 的櫃買對應
指數）發布 `Open/High/Low/Close/Change`。它**不接受任何參數**——`d=`、`date=` 和
`yr=/mn=` 都被忽略——而且不論名稱為何，永遠回傳當月資料，2026-09-16 時是 12 列。
因此櫃買指數 OHLC 在 2020–2026 保持 NULL，Step 27 的前向抓取可以從開始那天起累積。
audit §4.2 在這個 PR 中更正，包括先前導致錯誤結論的 404 探測。

## 基準，在寫任何程式之前量測

舊系統 `stock_db`，2020-01-02 → 2026-09-11：

| 量測 | 結果 |
| --- | ---: |
| `market_indices` 列數 | 449,528 |
| 不同的 TWSE 指數名稱 | 366 |
| 不同的 TPEx 指數名稱 | 43 |
| 日期數 | 1,627 |

實際來源的樣貌，2026-09-16 驗證：

| 量測 | 結果 |
| --- | --- |
| `MI_INDEX` 指數區段 | 6——價格和報酬，TWSE、跨市場和 TIP 各一 |
| 2026-09-11 的 TWSE 指數列數 | 273，沒有重複名稱 |
| TPEx `indexSummary` 區段 | 2，2020 → 2026 穩定 |
| TPEx 指數列數 | 74（2026-09-11）、60（2020-01-02） |
| 2026-01 的 `MI_5MINS_HIST` | 21 列，一個指數 |

## Identity 的發現

**TPEx 在兩個區段中重複使用同一個名稱**，所以只用發布的名稱不是 identity——而
audit §4.2 原本說是。

```text
指數段      櫃買指數   收市 395.52   漲跌  -9.72
報酬指數段  櫃買指數   收市 735.15   漲跌 -18.06
```

TPEx 的 34 個名稱中有 32 個同時出現在兩個區段。TWSE 恰好因為為報酬指數取了不同的
名稱而避開衝突——`發行量加權股價指數` 對 `發行量加權股價報酬指數`——但 identity
必須對兩種資料都成立，所以它是 `(source, section, published name)`。區段是結構性的，
而不是業務值：價格指數不會變成報酬指數，這正是 CLAUDE.md §51.5 對任何建構出來的 key
所用的檢驗。產生的 `index_code` 最長 49 個字元，在欄位的 64 以內。

舊系統 `market_indices` 有 43 個櫃買名稱，`櫃買指數` 每個交易日恰好出現一次，所以它
只保留了一個區段，**遺失了整個 TPEx 報酬序列**。在這裡那是新資料，不是對帳差異，
18-b 會照這樣回報。

## 驗收證據

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| 讀取每一個 TWSE 指數區段 | PASS | 從 6 個區段讀到 273 列。一個回歸測試斷言區段數量，所以只讀第一個會失敗。 |
| 指數 identity 在 TPEx 衝突下仍成立 | PASS | `櫃買指數` 被解析兩次，帶有不同的 `index_code` 值和不同的收盤；同一區段*內*重複的名稱仍會拋出 `ambiguous_identity`。 |
| adapter 指名它會重用的已儲存 resource | PASS | `stored_resource_key()` 回傳 `twse_mi_index:daily-quotes:<date>`，也就是 Step 17-c 存放 bytes 的地方。這是陳述，不是假設：生命週期沒有重新處理的路徑，所以 18-b 必須建一條。見下方的 review 發現。 |
| 沒有正負號的點數由它自己的欄位決定正負 | PASS | 寶島股價指數從 `865.63` 加上 `-` 標記解析為 `-865.63`；40 列為正，每一列的百分比都是正的。空白符號搭配非零幅度會拋出 `ambiguous_direction`，而 `X`（不比價）不儲存變動。 |
| 只有 TAIEX 有 OHLC | PASS | 整份清單的每一列 `open/high/low/trade_value` 都是 NULL；`MI_5MINS_HIST` 把 2026-01-02 解析為 `29016.68 / 29363.43 / 29007.75 / 29349.81`，不宣稱任何變動欄位，因為那份資料沒有發布。 |
| 其他所有情況都 fail closed | PASS | 休市日 → `no_data_for_date`；日期錯誤 → `date_mismatch`；header 改變 → `schema_mismatch`；區段內名稱重複 → `ambiguous_identity`；TAIEX 列在其月份之外 → `date_mismatch`。 |

## 驗證

```text
197 passed
```

本 step 之前的基準：172。差異就是這裡新增的 25 個 adapter 測試，每一個都確認過先
失敗——20 個在 adapter 存在之前，5 個來自 review，對照 push 時的程式。它們對照抓到的
回應 bytes 執行，不需要資料庫，這正是這道接縫的重點：這個 step 不寫入任何東西。

`ruff check` 相對於 `main` 沒有回報新問題；`ingestion/models.py` 中兩項既有的發現
不變。

## Code review 發現

六項發現，在改動任何東西之前都經過驗證；沒有一項是誤報。第一項推翻了這份報告的一個
宣稱。

| # | 發現 | 驗證方式 | 處置 |
| --- | --- | --- | --- |
| 1 | 借用價格匯入的 resource key 並不會重用它的 artifact：checkpoint 以 `import_id` 界定範圍，所以新的 id 會重新抓取全部 1,627 個檔案，而價格匯入自己的 id 會因為已完成的 checkpoint 而提前返回，不寫入任何指數列 | 閱讀 `_completed_checkpoint`（`lifecycle.py:495`）和 `_capture_raw`，後者是在抓取*之後*才以內容 hash 對 artifact 去重。只有一個 `run` 方法，沒有重新處理的路徑。 | **已修正，並撤回該宣稱。** adapter 有自己的 resource key，並透過 `stored_resource_key()` 指名已儲存的那個。重用是 Step 18-b 必須建立的生命週期能力；ROADMAP、audit 文字和這份報告不再說它已經存在。 |
| 2 | 區段以第一個欄位的標籤選取，所以改名會默默丟掉該日期 30–50 個指數 | 閱讀。 | 已修正。區段以其數值欄位辨識；符合的區段上出現無法辨識的標籤時拋出 `schema_mismatch`，沒有符合區段的檔案也一樣。 |
| 3 | 區段丟掉了它的標題所指名的提供者，所以 TWSE 和 TIP 發布的同名指數會讓整個交易日被 quarantine | 閱讀。潛在：273 個名稱，目前沒有重複。 | 已修正。區段是 `label/provider`——`指數/臺灣證券交易所`、`報酬指數/臺灣指數公司`。 |
| 4 | `_decimal` 對 `--` 回傳 None，而觀察會略過 None，所以 `close_value` 可能流到一個 NOT NULL 欄位 | 確認 `market_index_versions.close_value` 是 `nullable=False`。 | 已修正。缺少收盤在這裡拋出 `missing_value`，而不是在 18-b 的 writer 中變成 `IntegrityError`。 |
| 5 | 幅度在符號被驗證之前就被解析，所以沒有幅度、又無法讀取的符號會默默回傳 None | 閱讀；全市場 adapter 會先檢查標記。 | 已修正，而修正暴露了一個真正的風險：第一次嘗試移除了未知符號的檢查，卻沒有加上新的檢查，讓函式接受任何東西。回歸測試在 commit 之前抓到了那個狀態。 |
| 6 | TAIEX adapter 把每個非 OK 狀態都對應到 `no_data_for_date`，所以維護頁會在約 80 個月中被讀成空的月份 | 閱讀；指數 adapter 已經做了這個區分。 | 已修正。和其他地方一樣以結構判斷：沒有 `fields` key 代表沒有資料，其他任何狀態保持為 `source_status`。 |

真正重要的是第 1 項。它所支持的驗收標準——「TWSE 指數不需要新的抓取」——在這個
設計下不可能達成，而報告卻把它斷言為 PASS。它現在是 18-b 的要求，附有具名的掛鉤，
而不是平白宣稱的性質。

## 已確認的範圍排除

- 不寫入任何東西，也不出貨 migration：writer、source policy、涵蓋宣告、CLI 和
  backfill 都屬於 18-b。
- 官方估值屬於 18-c。
- 指數成交金額保持 NULL：檢查過的來源都沒有發布。
- 依上面的 spike，過去日期的櫃買指數 OHLC 保持 NULL。
- 沒有指數更名連結。改名的指數在官方證據另有說明之前是新的 identity，而目前沒有任何
  資料提供這種證據。
