# Step 19-b 驗收報告

狀態：IN REVIEW (#25)

範圍：TWSE 結果資料 adapter 及其明細頁

Schema 影響：無。Migration 影響：無。PIT 影響：無，因為不寫入任何東西。
`src/` 改動 +586/−11 行。

這個 step 新增三個清單 adapter（`TWT49U`、`TWTAUU`、`TWTB8U`）和兩個明細 adapter
（`TWT49UDetail`、`TWTAVUDetail`）。它也恢復了 19-a 省略的「列加明細」掛鉤：
`CorporateActionRow.detail_request`、`CorporateActionDetailRequest`、
`ParsedCorporateActionDetail`，以及基底 adapter 上的 `observation(row, detail=None)`。
TPEx 的列拒絕明細；TWSE 的除權息或減資列沒有明細就拒絕成為觀察。

## 基準

在 19-a 之前量測並記錄在它的報告中：舊系統 `dividend` 的全部 6,182 列
（2020-01-02 → 2026-09-11）都與 TWT49U 相符，數值差異為零。TWT49U 有 1,602 列是舊
系統從未保留的：1,402 個 ETF、170 個特別股和 30 個 TDR。這個 step 必須透過 adapter
重現這個結果。

程式從 19-a 拆分前被切出來的草稿開始。它保留在本機 branch `step-19-b-wip` 上，連同
2026-09-16 抓到的 TWSE fixture。

## 驗收證據

所有數字都來自 2026-09-16 抓取的逐年檔案，以 `executed_through = 2026-09-15` 透過
adapter 解析。

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| 每種 TWSE 資料的完整歷史中，`(code, locator)` 重複數為零 | PASS | `TWT49U`：7,792 個事件，35 個尚未執行。`TWTAUU`：149 個事件，8 個尚未執行，其中三個每個價格都是 `-`。`TWTB8U`：10 個事件。三種資料重複的 key 為零，重複的 `(code, date)` 也為零。 |
| 解析出的清單值等於舊系統 `dividend` | PASS | 舊系統有 6,182 列。每一列都透過 adapter 找到，沒有缺少的。比較前日收盤、參考價、權值+息值和類型，**差異為零**。期間內有 7,784 個官方事件，所以 1,602 個是新資料。 |
| `最近一次申報*` 和名稱絕不進入業務內容 | PASS | 改變一列的名稱，或三個最近申報欄位，其補完後的觀察仍然相等。一個測試以保留那些欄位的方式破壞 adapter，測試套件會失敗。 |
| TWSE 面額變更不儲存任何股數條件 | PASS | 2020-2026 的全部 10 個事件都對應到 `other`，`old_shares` 和 `new_shares` 為 NULL，價格保留。 |
| 對應在真實的明細頁上成立 | PASS | 2020-2026 間抽樣的 68 個明細全部能對應，沒有被 quarantine 的。53 個 `TWT49UDetail` 頁涵蓋 37 個普通股頁和 16 個特別股頁。15 個 `TWTAVUDetail` 頁涵蓋 8 個退還現金和 7 個彌補虧損的減資，包括 3356，它的減資與除息同時申報。 |

對應每一個明細頁（約 7,800 次請求）是 19-d 的工作；這個 step 以上面的樣本證明對應
正確。

## 驗證

```text
555 passed, 3 skipped
```

本 step 之前的基準：收集到 513 個。差異是這裡新增的 42 個 unit test，在 adapter
回來之前全部在收集時失敗。review 又加了三個（見下文）。

大部分 adapter 程式在草稿中已經存在，所以在收集時失敗本身證明不了多少。因此測試套件
也對照 22 個刻意對模組做的破壞執行。每個 TWSE 端的破壞都讓某個測試失敗。存活下來的
兩個破壞的是 TPEx 的防護，由 19-a 的測試套件抓到。有一個破壞一開始存活下來：

- **破壞：**移除「TPEx 的列不接受明細」的拒絕。
- **測試為什麼沒抓到：**它預期 `ValueError`，而實際觸發的 identity 檢查拋出的是
  `SourceDataError`，那是 `ValueError` 的子類別。
- **修正：**測試現在使用同一支證券的明細，並檢查錯誤訊息。

- `git diff --check`：乾淨。`alembic check`：沒有新的操作。
- `ruff check`：相對於 `main` 沒有新問題。`adapters/__init__.py` 中未排序的 `__all__`
  早於這個 branch。

## 已確認的範圍排除

- 沒有寫入、沒有 retraction、沒有 source policy、沒有 CLI；那些是 19-c 的工作。
- 沒有 backfill，也沒有抓取每一個明細頁；那些是 19-d 的工作。
- 沒有 ETF 分割資料；那是 19-e 的工作。
- 對 TWSE 面額變更，不從價格推斷任何股數比率。

## Code review 發現

兩項發現都成立，而且原因相同：解析後的明細頁沒有記錄它所回答的請求。頁面發布證券
代號但沒有日期，所以比對代號是唯一可能的檢查。

| # | 發現 | 檢查方式 | 處置 |
| --- | --- | --- | --- |
| 1（major） | 一列可能被同一支證券另一個事件的明細補完，例如在重試、快取命中或 queue 重新排序之後。它的 key 仍然正確，而條件卻是錯的。CLAUDE.md §51.5 要求這種情況 fail closed。 | 2543 在 2024 年除權息兩次。它五月的頁面，為十月的請求解析，毫無異議地補完了五月的列。 | **已修正。** `ParsedCorporateActionDetail.locator` 記錄請求的事件，而 `observation` 以 `invalid_identity` 拒絕任何 locator 不是該列的頁面。 |
| 2（minor） | 來自另一種 TWSE 資料的頁面拋出單純的 `KeyError`（`halt_date`），而不是 quarantine 理由。 | 把一個 TWT49UDetail 頁面傳給減資 adapter。 | **由同一個檢查修正。** 資料種類是 locator 的一部分，所以頁面在讀取任何值之前就被拒絕。 |

新增了三個回歸測試：頁面記錄它的 locator、同一支證券另一個事件的頁面失敗，以及另一
種資料的頁面失敗。後兩個在修正前失敗。移除 locator 比較會讓兩者再次失敗。
