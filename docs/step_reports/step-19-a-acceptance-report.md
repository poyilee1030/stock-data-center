# Step 19-a 驗收報告

狀態：IN REVIEW (#24)

範圍：結果資料契約、儲存精度，以及 TPEx adapter

Schema 影響：migration `8e4b2c7d9a13` 把 `corporate_action_versions` 的四個股數比率
欄位加寬到 `NUMERIC(28, 12)`，把 `official_rights_dividend_value` 從非負檢查中移除，
並對既有 revision 做雙向的重新 hash。downgrade 受保護。
PIT 影響：無。adapter 不寫入任何東西；唯一與 PIT 有關的決定 `executed_through`，是
在發出 job 時就固定的請求欄位。
`src/` 改動 +824/−27 行（其中 630 行是新的 adapter 模組）。

## 為什麼 Step 19 拆成五部分

六種資料、兩個 TWSE 明細頁、一次儲存更正、一個帶 retraction 語意的匯入，再加上一次
backfill，遠超過一個 pull request 能審閱的量（CLAUDE.md §1）。這個 step 的第一版草稿
包含全部六個 adapter，`src/` 底下約 1,400 行，所以沿著交易所的接縫再切一次：

| 部分 | 範圍 |
| --- | --- |
| 19-a | 契約、儲存更正、三種本身完整的 TPEx 資料 |
| 19-b | 三種 TWSE 清單資料及其兩個明細頁 |
| 19-c | 匯入路徑，含 retraction |
| 19-d | 2020-2026 的 backfill 與舊系統對帳 |
| 19-e | 途中發現的 ETF 分割資料（見下文） |

每一部分本身都是正確的。19-a 的契約已經指名全部六種結果資料，所以 19-b 新增 adapter
時不需要改動它。

## 基準，在寫任何程式之前量測

舊系統 `stock_db.dividend`，2020-01-02 → 2026-09-11：6,182 列，全部是 TWSE。
每種官方資料都在 2026-09-16 依日曆年 2020-2026 抓取。

| 量測 | 結果 |
| --- | ---: |
| TWT49U 以 `(code, date)` 對上的舊系統列 | 6,182／6,182 |
| 數值差異（前日收盤、參考價、權值+息值、類型） | **0** |
| 舊系統從未保留的 TWT49U 列 | 1,602——1,402 個 ETF、170 個特別股、30 個 TDR |
| 只在舊系統的列 | 0 |

這個基準是 19-b 要透過它的 adapter 重現的驗收證據。TPEx 沒有舊系統的對應：舊系統
從未抓過 TPEx 的公司行動資料，所以這裡每個 TPEx 事件都是新資料。

## 實際資料否證了什麼

這個 step 開始時的四個假設是錯的。每一個現在都是測試，ADR-0019 記錄了它迫使做出的
決定。

1. **結果檔案會列出還沒發生的事件。** 2026-09-16 抓取時，TWT49U 列出 35 個日期在
   09-16 或之後的列，`revivt` 有三個在 09-21，TWTAUU 則到 10-19，每個價格都是 `-`。
   Invariant G(2) 建立在事件已被執行的前提上。請求現在帶有 `executed_through`；之後
   的列只計數、不解析，而檔案只宣稱完整到那個日期為止——那也是 19-c 唯一可以做
   retraction 的範圍。
2. **`權值+息值` 有正負號。** 它的定義是前日收盤減參考價，而有六個價格高於收盤的現金
   增資把它發布成負數（TPEx 8444，2024-12-12：−0.204602）。錯的是 schema 的非負
   檢查，不是資料。
3. **股數比率需要十一位小數。** 每千股 `202.11906001` 股就是 0.20211906001。
   `NUMERIC(24, 8)` 會不報錯地存成 0.20211906；integration test 顯示 PostgreSQL 在
   migration 之前正是這樣做。TPEx 的現金股利帶八位小數，而 `TwdAmount` 把它限制在四位。
4. **TPEx 有面額變更端點。** audit §4.10 說沒有找到。`bulletin/pvChgRslt` 發布換股比率
   和前後兩個面額，2020-2026 的全部 13 個事件都滿足比率 = 舊面額 ÷ 新面額。同一天驗證
   的 TWSE `TWTB8UDetail` 則完全沒有發布比率。

## 驗收證據

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| 使用 1591/108/1 fixture 拒絕公告型資料 | PASS | fixture 包含兩列 TPEx 資料（董事會日期 1080806 和 1090505）。`ExchangeLocator` 以 `announcement_feed` 拒絕 `mopsfin_t187ap39_O`、`t187ap45_L`、`TWT48U` 和 `t05st09sub`，以 `unknown_feed` 拒絕任何未註冊的資料。 |
| `source_event_key` 是 `"<feed>:<locator date>"`，不含 revision 內容 | PASS | `exDailyQ:20240103`、`revivt:20240205`、`pvChgRslt:20240909`。改變一列的名稱、兩個價格、股利值、類型、現金和無償配股，它的 locator 都不變；改名則讓整個觀察不變。 |
| 不同事件有不同的 key | PASS | 6629 在 2024 年配發了四次：四個 key。重複的列會以 `ambiguous_identity` 把檔案送進 quarantine。 |
| 完整 TPEx 歷史中重複的 key 為零 | PASS | 2020-01-01 → 2026-09-15 經過 adapter：`exDailyQ` 7,328 列、`revivt` 108、`pvChgRslt` 13——重複的 `(code, key)` 為零。 |
| `executed_through` 之後的列不是事件 | PASS | 2026 年的 `revivt` 檔案解析到 2026-09-15：7 個事件，3 個計為尚未執行，涵蓋止於 2026-09-15。 |
| 每一列 TPEx 資料都能對應，或附上理由被 quarantine | PASS | 上面全部 7,449 列都能對應，**被 quarantine 的為零**：6,464 個除息、430 個除權、434 個除權息；87 個彌補虧損和 21 個退還現金的減資；13 個分割。 |
| 儲存的值能完整往返；downgrade 拒絕它無法容納的東西；歷史仍能去重 | PASS | Integration test：十一位小數的比率和負的差值都能完全讀回；對任一者，downgrade 都在修改之前拋出 `P0001`，head 和欄位精度不變；migration 之前寫入的 revision 在之後仍能去重，downgrade 後也取回它原本的 hash。從 migration 中移除重新 hash 會讓那個測試失敗。 |

### Fail-closed 路徑

每一條都有測試，每個測試都以移除其防護的方式檢查過：

- 區間回應或列落在請求的區間之外：`date_mismatch`
- 清單 header 或行內明細標籤改變：`schema_mismatch`
- 未知的事件類型或減資原因：`unknown_event_type`
- `ok` 以外的任何狀態：`source_status`
- 明細屬於另一支證券：`invalid_identity`
- 與明細矛盾的恢復交易日期：`date_mismatch`
- 明細單位與宣告的不同：`unit_mismatch`
- 有正負號的差值以外的負數金額：`invalid_numeric`
- 條件與類型矛盾，或有現金增資比率卻沒有價格：`inconsistent_terms`
- 退還原因卻沒有現金，或彌補原因卻有現金：`inconsistent_terms`
- 面額變更比率與面額矛盾：`inconsistent_terms`
- 單位從未見過的 `revivt` 現金增資：`unsupported_terms`

## 驗證

```text
510 passed, 3 skipped
```

本 step 之前的基準：收集到 472 個。差異就是這裡新增的 41 個測試（36 個 unit、5 個
integration）。第一版的 unit 測試套件和全部五個 integration test，都在其程式存在之前
跑紅過。之後 unit 測試套件為了拆分而改寫，對照的是已經存在的 adapter，所以跑紅對它
證明不了什麼。改為對照 19 個刻意對 adapter 做的破壞來執行：移除上面每一道防護、儲存
名稱、跳過每千股的除法、讓負數通過，以及對比率輸出 `1E+1`。每一個破壞都至少讓一個
測試失敗。

- `alembic check`：沒有新的操作。在本機資料庫上 downgrade 到 `7a2c9e4d1b58` 再
  upgrade 回來，執行乾淨。
- `git diff --check`：乾淨。
- `ruff check`：相對於 `main` 沒有新問題。

## 途中發現，排入後續而不是順手吸收

- **ETF 分割有自己的結果資料**：TWSE `rwd/zh/split/TWTCAU`（列出 0050 在 2025-06-18
  的分割）、TPEx `bulletin/etfSplitRslt` 和 `bulletin/etfRvsRslt`。Step 19 的六種資料
  都沒有列出這些事件，所以沒有它們，0050 的還原序列就是錯的。ROADMAP 現在有
  Step 19-e，而 Step 25 依賴它。
- **TWT49U 的 `最近一次申報*` 欄位是今天的申報**，不是事件的：2024 年的每一列都帶著
  `115年第2季`。audit §6 原本說要把它們放在 `source_terms`，那會讓每個過去的事件每季
  被修訂一次。audit 在這裡更正；19-b 的 adapter 排除它們。
- **ROADMAP 帳本漂移。** Step 18-b 以 #23 合併時，帳本上仍寫著 `THIS STEP`；在這裡
  更正。

## 已確認的範圍排除

- 沒有 TWSE adapter 或明細頁、沒有寫入、沒有 retraction、沒有 source policy、沒有
  CLI、沒有 backfill。
- `announcement_date`、`record_date`、`payment_date`、`earnings_stock_ratio` 和
  `capital_surplus_stock_ratio` 保持 NULL。
- 不從價格推斷任何比率，TWSE 面額變更或其他地方都一樣。

## Code review 發現

一項 `/code-review` 發現在檢查後成立。那次 review 中的另外三項宣稱不成立。

| # | 發現 | 檢查方式 | 處置 |
| --- | --- | --- | --- |
| 1 | `CorporateActionRangeRequest` 接受早於 `start` 的 `executed_through`。在 2027-01-01 請求 2027 年的檔案，會回報從 2027-01-01 到 2026-12-31 的涵蓋 | 閱讀 `coverage_end` | **已修正。** 請求現在拒絕這種情況：這樣的 job 只能計數，所以發出者會跳過它。一個回歸測試在修正前失敗。 |
| — | `CorporateActionObservation.__post_init__` 中的 `money` 和 `ratios` 沒有被使用 | `models.py:290` 在「至少一個條件」的檢查中仍讀取兩者 | 不是缺陷。 |
| — | revivt 2026：沒有人確認被丟掉的三列是未來日期 | `test_rows_dated_after_executed_through_are_not_events_yet` 斷言 2026-09-15 之後恰好三列，且 `not_yet_executed == 3` | 已經涵蓋。 |
| — | Ruff：`ingestion/models.py` 中的 I001 和未使用的 `ArtifactOrigin`，`market_reference/models.py` 中的 TRY004 | 在 `main` 上執行 ruff | 三者都已存在於 `main`，所以不在範圍內。 |
