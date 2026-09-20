# Step 22-a 驗收報告

狀態：MERGED (#36)

範圍：月營收的第一部分，已發布比較值的 schema 與 MOPS adapter。這個 step 新增：

- migration `d4a7f2c9b8e1`
- MOPS `t21sc03` adapter：`mops_t21sc03_sii`、`mops_t21sc03_otc`，各含 `_0`／`_1` 頁
- importer
- CLI `monthly-revenue`

backfill 與對帳是 22-b，發布證據是 22-c。

Schema 影響：`monthly_revenue_versions` 新增八個可為 null 的欄位：`revenue_last_month`、
`revenue_last_year_month`、`mom_pct`、`yoy_pct`、`cumulative_revenue`、
`cumulative_revenue_last_year`、`cumulative_yoy_pct`、`note`。另外新增
`stockdc_monthly_revenue_hash`，以及兩個來源的宣告。downgrade 在已存有比較值，或兩個
來源已有 ingest run、manifest、版本時，會在修改前拒絕（§81）。

PIT 影響：沒有新證據。兩個來源只接受 `official`，也不宣告 release rule，所以每個
版本都記 `unknown`：只有 System PIT 可見，Market PIT 看不到。這是偏晚、不是偏早。
證據由 22-c 附加。

規模：`src/` +615／−4 行，低於約 800 行的拆分門檻（`CLAUDE.md` §1）。另外提交一個
migration、七個 fixture 和兩個測試檔。

## 為什麼 Step 22 拆成三部分

owner 於 2026-09-20 決定拆成三部分：22-a schema 與 adapter、22-b backfill 與對帳、
22-c 證據。這個接縫讓每部分本身都正確：沒有證據時版本是 `unknown`，Market PIT 看不到。
若只附加 release rule、沒有復原的公告日期與 legacy 首見紀錄，晚於 10 日才公告的
公司會被提早看到，那才是前視偏差。

同一天 owner 也改了兩條驗收，ROADMAP 已同步：

- **刪除 revswarm 交叉核對。** revswarm 已把可靠的結果寫回 `market.csv`，這個 step
  不需要 revswarm。
- **改寫「不早於 release rule」那條。** 原文與「在公告日結束時解析」互相矛盾，改為只約束
  落在 10 日、以 release rule 解析的列。

## 這個 step 中做出的來源決策

每項決策都記錄在 ROADMAP Step 22-a 和 audit §4.7「Step 22-a findings」。

1. **每個市場一個來源。** 原本以為一個 `mops_t21sc03` 來源就夠了。舊系統的 5236
   在 2026M06 同時出現在上櫃與上市的頁面；如果只有一個來源，同一個邏輯鍵會有兩個
   版本交替出現（CLAUDE.md §30）。所以改成 `mops_t21sc03_sii` 與 `mops_t21sc03_otc`。
2. **比較值只在有值時才進 hash。** `revenue` 與 `currency` 的 hash 算法和原本相同，
   比較值去掉 NULL 後接在後面。所以沒有比較值的既有版本，身分完全不變，也不必改寫
   任何已存的列。有測試確認這一點。
3. **照原樣儲存。** 百分比照印出的樣子儲存，只去掉千分位（例如 `4,533.33`），空白為 NULL。
   備註逐字儲存，`-` 也保留。上月營收和上個月的當月營收不做對帳。
4. **KY 發行公司。** 使用者問舊系統為什麼沒有抓 KY：舊系統爬蟲的網址寫死成 `_0` 頁
   （`scraper/monthly/fetch_monthly_revenue.py:18`），從未請求 `_1` 頁。所以舊系統
   `monthly_revenue` 裡 KY 是 0 列，但 `daily_quotes` 有 140 支 KY 證券。
5. **不是來源回答的內容。** 主機曾回應 18 bytes 的 `Unreachable Server`（HTTP 200），
   adapter 以 `unusable_response` 讓整頁失敗（與 MOPS 外資持股 adapter 同一個代碼）。
   處理方式與 20-a 的 TWSE CDN 錯誤頁相同：隔離並保留 raw。隔離後的 checkpoint 不是
   `captured`，所以用同一個或新的 import id 重跑都會重新抓頁面（有測試）。原本打算讓共用 lifecycle
   自動重抓一次，但那會一併改變其他資料集主資源的行為，已撤回。

## 驗收證據

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| 已發布的比較值照發布的樣子儲存，絕不與我們的序列對帳；2026M06/M07 是回歸 fixture | PASS | `test_a_comparative_that_disagrees_with_the_prior_month_is_stored_as_published`：6441 廣錠 M06 以舊系統首次抓到的 13,094 千元寫入，M07 頁的上月營收 10,948 照原樣儲存，匯入成功。今天的 M06 頁已經是更正後的值（兩頁 0 家不一致），所以 audit §7.3 的 11 家差異只存在於舊系統首見值與今天的頁面之間；完整的 11 家要等 22-c 匯入舊系統首見值後才會出現。 |
| 兩次抓取之間的更正產生 revision | PASS | `test_a_correction_between_two_fetches_is_a_revision`：同一頁抓兩次，只有 6441 產生新版本（13,094,000 → 10,948,000），其餘 860 列去重。 |
| `_1` 頁的 KY 發行公司以新涵蓋出現 | PASS | 上市 2026M07 的 `_1` 頁有 94 家（例如 5871 中租-KY 營收 8,463,270 千元），`_0` 頁沒有 5871。 |
| 沒有比較值的版本保留 22-a 之前的 hash | PASS | `test_a_version_without_comparatives_keeps_its_pre_22a_hash`：新函式對全為 NULL 的比較值算出的 hash，與原本 `jsonb_build_object('revenue','currency')` 的 hash 相同。 |
| adapter 能解析整段期間的真實頁面 | PASS | 即時抓取 2020–2026 每年 1 月與 2026M08，兩個市場各 `_0`／`_1`，共 32 頁全部解析成功，header 都是同一種（例如上市 `_0`：2020M01 902 家 → 2026M08 992 家；上市 `_1`：73 → 94 家）。期間遇到一次 HTTP 502，重試後成功。 |

## 驗證

從零 migrate 的資料庫：

```text
931 passed, 3 skipped, 1 warning
```

`main` 上的基準是 896 passed，差異是 35 個新測試：21 個 unit、14 個 integration。
code review 修正再加 3 個 unit test（見下一節），共 38 個。

每個測試如何確認先失敗：

- **Unit test：** adapter 存在之前，在收集階段就失敗；之後對照 stub（`parse` 直接
  失敗、`resource` 回空 URL），20 個失敗，只有來源常數的測試通過。另外兩條列形狀
  測試（缺一格、代號格式錯）在收緊解析之前失敗。
- **Integration test：** importer 存在之前，在收集階段就失敗；移開 migration 時 12 個
  失敗。其餘 2 個（尚未公布的月份被隔離、manifest 記錄單位）只在收集階段失敗過。
- **Contract test：** `test_pr14_storage_contract_source_coverage` 的兩個 unit test
  在 inventory 更新前失敗：新欄位缺來源條目，舊系統欄位對應還帶著 `planned_pr`。

`ruff check` 對新增和改動的檔案沒有回報新問題（`adapters/__init__.py`、`models.py`
的既有問題在 `main` 上就存在）。

## Code review（#36）

review 沒有找到正確性問題。它提到一點但沒有列為 finding，owner 判斷應該修，已在本 step
修正：

- **`_0` 與 `_1` 的標題相同，adapter 分不出來。** review 認為伺服器回錯頁時「只會多出
  重複列、被去重吸收」，這個推論不成立：`_0` 的請求如果拿到 `_1` 的內容，KY 列確實會被
  去重，但那次執行的國內公司會整批缺漏，manifest 卻記成成功。這是靜默的涵蓋缺口，要到
  22-b 對帳才看得到。每一頁的全市場合計列會寫明是國內還是國外（`全部國內上市公司合計`／
  `全部國外上市公司合計`），adapter 現在會檢查這一列，對不上就讓整頁以 `page_mismatch`
  失敗；adapter 版本改為 v2。3 個 unit test 在修改之前失敗：`_1` 內容答 `_0` 請求、
  `_0` 內容答 `_1` 請求、合計列缺漏。整段期間的 32 頁真實頁面在 v2 下重新解析，全部
  通過。

## 踩到的坑

- **原本以為 big5 可以解碼。** strict `big5` 在 0xF9 byte 失敗，要用 `cp950`。
- **百分比也有千分位。** 第一版 adapter 的百分比格式沒有逗號，碰到 `4,533.33` 就讓
  整頁失敗；由 2330 的測試抓到。
- **格數不對的列會被靜默丟掉。** 第一版只挑「11 格且代號合法」的列，其他列直接略過。
  現在只有單一空白格的版面列可以略過，其他形狀一律讓整頁失敗。
- **lifecycle 的 `evidence_status`。** 原本把來源宣告為 `unverified`，但 lifecycle
  要求 `verified`，所以改成 `verified`；證據型別仍只有 `official`。

## 已知限制與延後的工作

- **22-b：** backfill 與對帳；每個月份確認 `_0`／`_1` 不重疊。
- **22-c：** 發布證據。開工時要決定只在 `_1` 頁出現的 KY 發行公司用哪種證據，因為舊系統
  沒有它們的日期。
- **修正帳本：** ROADMAP Step 21-b 與其報告已改為 **MERGED** (#35)。
