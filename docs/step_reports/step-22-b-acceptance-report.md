# Step 22-b 驗收報告

狀態：IN REVIEW (#37)

範圍：月營收的第二部分，歷史 backfill 與舊系統對帳。這個 step 新增：

- migration `b7e4c1a95d38`（月度涵蓋宣告，以及生成欄位 `revenue_period`）
- `MonthlyRevenueBackfill`：走過月份 × 頁，可續跑、有節流、逐頁回報
- CLI `monthly-revenue --through` 與 `--page both`
- `scripts/reconcile_monthly_revenue.py`

發布證據是 22-c。這個 step 寫入的版本仍然只帶 `unknown`：只有 System PIT 看得到，
偏晚而不偏早。

Schema 影響：`monthly_revenue_versions` 新增 `revenue_period`（由 `revenue_year`
與 `revenue_month` 生成的 `date`）與索引 `ix_monthly_revenue_versions_period`，
以及 `dataset_expected_coverage` 的兩列宣告。兩者都是衍生物：欄位由留下來的欄位
生成，宣告陳述的是期望而不是已匯入的事實，所以 downgrade 直接移除、不損失任何歷史
（CLAUDE.md §81）。

PIT 影響：沒有。涵蓋宣告不改變任何版本的可見性。

規模：`src/` +326／−11 行，低於約 800 行的拆分門檻。另加一個 migration、一支對帳
腳本與兩個測試檔。

## 這個 step 中做出的來源決策

每項決策都記錄在 ROADMAP Step 22-b 與 audit §4.7「Step 22-b findings」。

1. **月份用生成欄位表示。** 涵蓋宣告只認一個 period 欄位，而版本表用
   `(revenue_year, revenue_month)` 當鍵。另外寫一份日期副本會有跟月份對不起來的
   可能，而「能對不起來的欄位」做出來的涵蓋報告，會把那個矛盾當成涵蓋來報。所以
   `revenue_period` 是 `GENERATED ALWAYS AS (make_date(...)) STORED`，寫不進去。
2. **`calendar_market` 填 `TWSE`，但不會被查。** 宣告表要求有一個日曆市場，月報表
   卻不是按交易日發布的。決定「不查日曆」的是 `cadence = 'calendar_month'`。
3. **涵蓋是月份的性質，不是頁的性質。** `_1` 不是另一個資料集；某個月份沒有任何
   外國發行公司也不該讀成缺口。頁層級的結果在 backfill 報告與每頁自己的 manifest 裡。
4. **`no_data` 與失敗分開計數，但兩者都不算涵蓋。** 未發布的月份回答 查無資料，
   重跑修不好它；失敗重跑修得好。兩者都讓 `is_complete` 為 false。
5. **來源會按發行公司的現況重寫自己的歷史。** 這是這個 step 最大的發現，見下節。

## 原本以為只是「舊系統少了 KY」

原本預期的差異只有一種：舊系統從來沒抓過 `_1` 頁。實際跑完對帳後，差異有九種，
其中兩種是對稱的同一件事：

`t21sc03` 是重新產生的頁面，列出的是**現在**具備該身分的發行公司。所以

- 已經離開兩個市場的發行公司，連 2020 年的頁面上都不再出現（540 列）。手動核對
  2020M01：2867 三商壽與 6806 森崴能源出現在 `pub` 頁，3454 晶睿四個頁面都沒有。
  `pub`／`rotc` 依 owner 決定不在 v1 範圍內，所以這些列不是我們涵蓋範圍內的缺口。
- 反過來，今天的頁面也帶著發行公司上市**之前**的月份（1,257 列），舊系統當時沒有
  任何頁面可以讀到它們。

同一現象也解釋了 77／78 列的「舊系統記在另一個市場」。

## 驗收證據

對帳指令（`stockdc_backfill` 對上舊系統 `stock_db`，2020-01 → 2026-08）：

```bash
python scripts/reconcile_monthly_revenue.py \
    --database-url postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_backfill \
    --legacy-database-url postgresql+psycopg://user:password@127.0.0.1:5419/stock_db
```

結束碼 0：每個差異都落在有證明或被逐列列名的類別裡。

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| 舊系統 `monthly_revenue` 已對帳 | PASS | 兩個來源共比對 140,345 列（上市 75,495、上櫃 64,850），每個差異都分類（下表）。腳本結束碼 0：類別集合 ⊆ `EXPLAINED`。 |
| 兩個市場 × 兩頁的涵蓋完整 | PASS | Step 16 涵蓋報告：兩個市場各 80／80 個月份，`missing` 與 `unexpected` 都是空的。320 頁全部匯入（每個市場 `_0` 80 頁、`_1` 80 頁），隔離 0 筆，`no_data` 0 筆。上市 82,859 列、上櫃 67,782 列。 |
| 每個月份的 `_0` 與 `_1` 頁沒有共同的公司 | PASS | 160 個 market-month 的 raw artifact 全部重新解析後取交集：`months_with_a_company_on_both_pages` 為空。unit test `test_the_two_pages_of_one_month_share_no_company` 是同一件事的 fixture。 |
| `_1` 頁面的 KY 發行公司以新涵蓋出現 | PASS | 上市 94 家、上櫃 30 家外國／KY 發行公司，合計 8,951 列，舊系統一列都沒有（`absent_from_legacy:foreign_page` 6,647 + 2,304）。 |

差異分類（上市／上櫃）：

| 類別 | 上市 | 上櫃 | 證明 |
| --- | --- | --- | --- |
| `absent_from_legacy:foreign_page` | 6,647 | 2,304 | 該月 `_1` 頁的公司；舊系統從未請求 `_1` |
| `absent_from_legacy:issuer_listed_after_that_month` | 629 | 628 | 舊系統最早的月份晚於此月，且我方最早的價格日期也晚於此月 |
| `absent_from_legacy:legacy_filed_it_under_the_other_market` | 77 | 0 | 舊系統同月把它記在另一個市場 |
| `absent_from_legacy:legacy_missed_that_month` | 11 | 0 | 舊系統在此月前後都有這家公司 |
| `absent_from_source:issuer_on_no_page_of_either_market` | 230 | 310 | 兩個市場所有月份的頁面都沒有它 |
| `absent_from_source:stored_under_the_other_market` | 0 | 78 | 同月我方把它存在另一個市場的來源下 |
| `note_differs:legacy_collapsed_whitespace` | 6 | 16 | 收斂連續空白後兩者相同 |
| `note_differs:legacy_lost_bytes_decoding` | 29 | 40 | 舊系統字串帶 U+FFFD |
| `note_differs:legacy_decoded_the_same_bytes_differently` | 1 | 0 | 兩者編回 cp950 是同一串位元組（`‧`／`•` 都是 `A1 45`） |
| `note_differs:legacy_read_NA_as_missing` | 6 | 0 | 頁面備註是字面的 `NA`，舊系統是 NULL |
| `value_differs:legacy_capture_stale` | 103 | 63 | 次月頁面的上月營收等於我方的值，不等於舊系統的 |
| `value_differs:restated_by_a_correction` | 634 | 352 | 該比較值的輸入月份中有上一列證明過的更正 |
| `value_differs:legacy_disagrees_with_the_month_it_restates` | 0 | 3 | 我方的值等於我方該月的值，舊系統的不等於 |
| `value_differs:legacy_row_internally_inconsistent` | 1 | 1 | 舊系統的累計不等於它自己的前月累計 + 當月營收，我方的相等 |
| `value_differs:follows_the_amounts_it_is_computed_from` | 1 | 3 | 同列的兩個金額已被解釋 |
| `value_differs:known_late_republication` | 13 | 3 | 逐列列名（見下） |

## 十六列沒有機械證明的差異

`KNOWN_LATE_REPUBLICATION` 逐列列名，全部是 2026 年、全部沒有改到 當月營收：

- 2425（2026M01–M05，備註）：改寫備註，加上停業單位金額。
- 1235（2026M04，備註）：備註裡的金額從 39,332 改成 31,532 仟元。
- 2887 台新新光金（2026M04）：合併後重編去年當月營收、去年累計營收與兩個 yoy。
- 2608（2026M08）：重編去年當月營收、去年累計營收與 yoy。
- 6692（2026M05，備註）：把「增加」改成「減少」。
- 4402（2026M06）、6101（2026M07）：備註整段換成較長的說明。

兩邊資料庫都沒有任何東西能說出舊系統讀到那一頁的當天，頁面上寫的是什麼，所以不給
規則，只逐列列名。新出現的同類差異不會落進這個類別，會是 `unexplained` 並讓腳本
以 1 結束。

## 驗證

從零 migrate 的資料庫：

```text
951 passed, 3 skipped, 2 warnings in 367.33s
```

其中 17 條是這個 step 新增的：15 個 integration、2 個 unit。

每個測試如何確認先失敗：

- **Integration（15 個）：** `MonthlyRevenueBackfill` 存在之前，整個檔案在收集階段
  就失敗。之後逐項確認：移掉 migration 的宣告時，涵蓋與宣告的 4 條失敗；`pages`
  參數未實作時走遍兩頁的 3 條失敗；`no_data` 尚未與失敗分開時該條失敗；CLI 還沒有
  `--through`／`--page both` 時該條以 `argparse` 錯誤失敗；生成欄位還沒加上時，
  `revenue_period` 那條在 SQL 就失敗；downgrade 那條在 migration 之前失敗。
- **Unit（2 個）：** 兩頁不重疊的 fixture。**這兩條沒有紅過**：它們斷言的是來源本來
  就成立的性質（`_0` 與 `_1` 不重疊、5871 只在 `_1`），寫下來是當回歸 fixture 用，
  不是先寫測試再實作的那一類。
- **既有測試的修正：** `test_alembic_metadata_has_no_drift` 在把索引寫進
  `metadata.py` 之前失敗（migration 建了索引、metadata 沒宣告）。

`ruff check` 對新增與改動的檔案沒有回報問題。

## 踩到的坑

- **`alembic check` 只在從零 migrate 的資料庫上抓得到漏宣告的索引。** 全套測試
  第一次跑出 6 個失敗，其中計數全表的那幾條是本機長年重用的 `stockdc` 測試庫殘留；
  重建那個資料庫之後只剩下真正的一條：migration 建了索引、`metadata.py` 沒宣告，
  `test_alembic_metadata_has_no_drift` 因此失敗。
- **502 之後不能原地續跑。** manifest 會比對 `git_commit`，而 backfill 跑完之後
  工作區已經變髒，用同一個 base import id 重跑會被「import_id cannot be reused with
  changed configuration」擋下。兩頁各用一個新的 import id 重抓（22-a 報告裡寫的做法）。
- **原本以為差異只有 KY。** 見上面那節；來源會按現況重寫歷史，是這個 step 才看見的。
- **原本以為備註不會有差異。** 98 列的差異全部出在舊系統自己的解碼與 CSV 處理，
  不是頁面變過。

## 已知限制與延後的工作

- **22-c：** 發布證據。開工時要決定只在 `_1` 頁出現的 KY 發行公司用哪種證據，因為
  舊系統沒有它們的日期。
- **`pub`／`rotc` 的 540 列**依 owner 決定不在 v1 範圍內，不會補。
- **對帳腳本沒有單元測試**，與其他 step 的 `scripts/reconcile_*.py` 一致：它的輸出
  本身就是證據。
