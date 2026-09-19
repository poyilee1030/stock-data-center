# Step 20-d 驗收報告

狀態：IN REVIEW

範圍：外資持股。這個 step 新增 TWSE `fund/MI_QFIIS`（`twse_mi_qfiis`）和 MOPS
`t13sa150_otc`（`mops_t13sa150_otc`）adapter，以及 backfill 期間依 owner 決定加入
的第二個 TPEx 來源 `insti/qfii`（`tpex_insti_qfii`）。範圍內還有 importer、source
policy 與涵蓋宣告、CLI `foreign-holding`、三個來源 2020-01-02 → 2026-09-11 的
backfill，以及與舊系統 `foreign_holding` 的對帳。

Schema 影響：無。`foreign_holding_versions` 從 Step 7 起就存在，每個欄位都有來源。
兩個 migration 只新增列：`e7a9c3f1b2d4` 宣告 `foreign_holding` catalog、TWSE 與
MOPS 兩個來源、它們的 release rule 對應和兩個預期涵蓋宣告；`f2b6d8a4c1e9` 宣告
`tpex_insti_qfii` 和它的 release rule。兩者的 downgrade 都有防護。
PIT 影響：沒有新的影響。三個來源都遵循 `exchange_daily_settled@1`，而這個資料集
加入 `DATASET_TARGETS`，所以重新匯入時，已儲存的抓取仍會否證規則（#30 的發現）。
規模：`src/` 改動 +820/−0 行，略超過約 800 行的拆分門檻（`CLAUDE.md` §1）。
`insti/qfii` 原本可以是獨立的部分，但它是 backfill 途中發現、依 owner 決定併入
本 step 的；其餘部分（兩個 adapter、importer、CLI）已是最小的接縫，一個資料集。
另外提交一個對帳腳本和九個 fixture。

## 這個 step 中做出的來源決策

每項決策都記錄在 ROADMAP Step 20-d 和 audit §4.4「Step 20-d findings」。

1. **MOPS 以 cp950 解碼。** 頁面是 MS950：嚴格的 big5 在幾個證券名稱上失敗
   （安碁、宏碁……），cp950 能解碼整頁。名稱不儲存。
2. **TWSE 的比率以精確的十進位讀取。** `MI_QFIIS` 的兩個持股比率是 JSON 數字，
   其他值都是字串；用 float 讀，0.3 會變成 0.29999…。
3. **異動原因是一組代碼。** `與前日異動原因` 是 2–5 的單一數字代碼，定義在頁面自己
   的註解中；空白代表一般的市場交易異動。一格可以有多個代碼：TWSE 每個代碼一個
   連結，以 `<br>` 分隔（2303，2020-05-15：`2<br>4`）；MOPS 把數字連在同一個連結裡
   （5483，2020-04-06：`24`）。兩者都儲存為遞增、逗號分隔（`2,4`），空白為 NULL。
   連結指向每月換 URL 的申報頁，不儲存，所以連結改變不是 revision（有測試）。
   未知的數字或重複的代碼會讓檔案失敗。
4. **TWSE 最近申報日期的整數 0 代表還沒有申報。** 尚未申報的新上市證券，這一欄
   是 JSON 整數 `0`（4581，2020-03-06），MOPS 則留白。兩者都存 NULL；這一欄以外
   的任何非文字值仍會讓檔案失敗。
5. **MOPS 回溯時有生存者偏差，所以 TPEx 有兩個來源。** 見下一節。

## MOPS 的生存者偏差

MOPS 以**今天**的證券清單重建每一個過去的日期。第一輪對帳時，舊系統有 7,529 列是
我們 MOPS 沒有的，全部屬於五支證券：

| 證券 | 最後一個 TPEx 交易日 | 舊系統的列數 |
| --- | --- | ---: |
| 5371 中光電 | 2026-08-21 | 1,620 |
| 4130 健亞 | 2026-07-21 | 1,593 |
| 3426 台興 | 2026-06-01 | 1,559 |
| 4987 科誠 | 2026-05-20 | 1,553 |
| 5236 凌陽創新 | 2026-07-15 轉到 TWSE | 1,204 |

它們在 2020-01-02 起的每一個 MOPS 日期都不見了；舊系統的檔案是 2026 年 2 月抓的，
那時它們還在 TPEx 交易。舊系統本身也有同樣的偏差，只是早一次：49 支在 2026-02
之前離開 TPEx 的普通股（例如 3202、6589、6287），完全不在舊系統中。

TPEx 自己的 `insti/qfii` 在過去的日期仍列出這些證券（2020-01-02 有 5371、4130、
3426、4987）。依 owner 決定（2026-09-19），它存成第二個 TPEx 來源
`tpex_insti_qfii`，兩個來源各自保存歷史、不合併（CLAUDE.md §30）。`insti/qfii`
缺少大部分 ETF，也沒有發布陸資法令投資上限比率、異動原因和最近申報日期，這三欄在
該來源保持 NULL；它的 `排行`、`名稱` 和 `備註`（空白、`禁止投資` 或 `已達上限`）
沒有契約欄位。MOPS 仍是 TPEx 宣告的涵蓋來源。消費端如何在兩個 TPEx 來源之間選擇，
留給 Step 28 或另一份 ADR。

## 基準

舊系統 `stock_db.foreign_holding`，2020-01-02 → 2026-09-11：

| 市場 | 列數 | 日期數 |
| --- | ---: | ---: |
| `sii` | 1,636,432 | 1,627 |
| `otc` | 1,296,284 | 1,627 |

舊系統只存九個契約欄位中的六個（股數三欄、兩個比率、共用法令上限），型別是 float。
陸資上限、異動原因和最近申報日期沒有舊系統欄位。

## 執行

```text
                     versions   securities  dates   raw artifacts   evidence (release_rule)
twse_mi_qfiis       1,932,630        1,427  1,627           1,726   1,932,630, unknown 0
mops_t13sa150_otc   1,446,062        1,045  1,627           1,716   1,446,062, unknown 0
tpex_insti_qfii     1,331,502          949  1,627           1,706   1,331,502, unknown 0
raw artifacts 4,881, 1.14 GB
```

三個來源的涵蓋都是 1,627／1,627 個日期（Step 16 日曆的預期日期）。

backfill 跑了幾輪，manifest 記錄了每一輪：

1. **v1**（TWSE 和 MOPS）在約 100 個日期後手動停止：它在 2020 年就碰到了多代碼的
   異動原因和 TWSE 的整數 0 日期，而 adapter 讓那些檔案失敗，而不是猜。
2. **v2**（TWSE 和 MOPS）以新的 import id 從頭跑完。TWSE 1,626 個日期匯入，1 個
   逾時；MOPS 1,618 個日期匯入，9 個 `502 Bad Gateway`。以同一個 import id 重跑，
   只重新抓取那 10 個失敗的日期，兩者都完成。
3. **`insti/qfii` v1** 1,548 個日期匯入；79 個日期（2020-05-04 → 2020-08-24）因為
   6497 的 `備註` 是未知的 `已達上限` 而失敗。adapter 改為 v2 接受這個旗標之後，以
   新的 import id 重新匯入這 79 個日期。

失敗的 manifest 和 quarantine 列作為歷史保留。MOPS 還有一個停在 `running` 的
manifest，是 v1 被停止時正在處理的那個日期；它沒有寫入任何業務列。

## 驗收證據

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| 舊系統 `foreign_holding` 在兩個市場都已對帳 | PASS | **TWSE：**比較 1,634,573 列，**差異為零**；另有 1,859 列落在兩個舊系統存了別的日期檔案的日期（2022-06-28、2024-12-19，一致率 0%）。**TPEx：**MOPS 比較 1,288,755 列，差異為零；`insti/qfii` 比較 1,295,291 列，差異全部已分類（見下文）。 |
| 每個差異都已分類 | PASS | `scripts/reconcile_foreign_holding.py` 以 0 結束：只要還有未分類的 `legacy_only`、`value_differs` 或任何算術失敗，它就以 1 結束。 |
| 涵蓋完整 | PASS | 三個來源都是 1,627／1,627 個日期。 |
| 每個儲存的列都滿足來源自己的算術 | PASS | E = trunc(C / A, 2) 在每個來源的每一列成立；D = trunc(B / A, 2)（TWSE、MOPS）或 round(B / A, 2)（`insti/qfii`）；B + C ≤ floor(A × F)。4,710,194 列中唯一的例外是已命名的來源異常（`insti/qfii` 2026-04-07 的 6028，見下文）。 |
| 每個 MOPS 請求都經過 20-c 的控管器 | PASS | `test_every_mops_request_is_paced_by_the_host_budget`；實際 backfill 每個 MOPS 日期約 3 秒。 |
| MOPS 的 POST 請求記錄在 provenance 中 | PASS | `test_a_mops_import_records_its_post_request_in_provenance`：manifest scope 和 run metadata 都還原成實際送出的 resource。 |

## 差異

**TWSE — `legacy_captured_another_date`：2 個日期，1,859 列。** 2022-06-28 和
2024-12-19，舊系統的值與我們同一天的值一致率為 0%：舊系統存了別的日期的檔案。這是
Step 18-c 和 20-a 已經找到的同一類舊系統缺陷。

**MOPS — `legacy_only:mops_omits_security_no_longer_listed`：7,529 列。** 上一節的
五支證券。對帳的規則是：MOPS 在整段期間都沒有這支證券的任何一列。抽樣的每一列，
`insti/qfii` 在同一天都有它。

**`insti/qfii`：**

| 類別 | 列數 | 說明 |
| --- | ---: | --- |
| `investable_ratio_rounded_not_truncated` | 648,913 | 股數完全相同；D 是四捨五入，舊系統（MOPS）是截斷 |
| `legacy_only:qfii_omits_security_listed_in_mops` | 992 | `insti/qfii` 沒有、而 MOPS 在同一天有的證券（大部分是 ETF） |
| `value_differs:qfii_disagrees_with_mops_same_date` | 327 | 全在 2026-04-07；舊系統等於 MOPS，`insti/qfii` 不同 |
| `legacy_only:before_security_first_appears_in_source` | 1 | 5236 的 2021-07-28，它第一個交易日的前一天 |

2026-04-07（清明連假後第一個交易日）的 `insti/qfii` 檔案本身不一致：它與同一天的
MOPS 只有 549／874 支證券一致，與它自己前一個交易日（04-02）只有 141 支一致，而
6028 那一列的 B 停在 25,000,000，C 卻已是 1,000，違反它自己的 B = A×F − C（MOPS
同一天的 B 是 24,999,000）。兩個來源各自儲存、照發布的樣子；6028 列在腳本的
`KNOWN_SOURCE_ANOMALIES`，新的異常仍會讓對帳失敗。

**我們有、舊系統從未有過的列。** TWSE 295,666 列、MOPS 157,246 列、`insti/qfii`
36,210 列屬於完全不在舊系統中的證券：舊系統沒有收集的商品（ETF 等），以及在舊系統
2026-02 抓取前就離開 TPEx 的證券。另有 MOPS 61 列、`insti/qfii` 1 列是舊系統在某些
日期缺少、但在其他日期有的普通股；它們不影響對帳結論，腳本照實回報。

## 驗證

從零 migrate 的資料庫：

```text
809 passed, 3 skipped, 1 warning
```

`main` 上的基準是 738 passed。差異是 71 個新測試：52 個 unit、19 個 integration。

每個測試如何確認先失敗：

- Unit test：在 adapter 存在之前就在收集時失敗；之後對照一個拋出
  `NotImplementedError` 的 stub，28 個失敗（只有 source code 常數的測試通過，因為
  stub 帶有它們）。backfill 途中新增的格式（多代碼、整數 0 日期、`已達上限`）各自先
  寫測試、看它失敗，再改 adapter 並提高版本。
- `insti/qfii` 的 11 個 unit test 對照 stub 失敗；它的 4 個 integration test 在
  migration `f2b6d8a4c1e9` 之前失敗。
- Integration test：在 importer 存在之前就在收集時失敗；有 importer、沒有 migration
  和 CLI 時，8 個失敗。晚到抓取的測試在移除 `DATASET_TARGETS` 條目時失敗（1,362 列
  `release_rule` 洩漏到 `capture_bound` 旁邊），恢復後通過。
- 有三個保護性測試（`29`、`22`、`2 x` 這類不合法的異動原因）在舊程式上就已被
  拒絕，所以一開始就通過；它們守住的是新的多代碼解析仍要拒絕這些值。

`ruff check` 對新增和改動的檔案沒有回報新問題；`adapters/__init__.py` 未排序的
`__all__` 和 `institutional_financing/ingestion.py` 的 import 排序在 `main` 上就已存在。

重現對帳：

```bash
python scripts/reconcile_foreign_holding.py \
    --database-url postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_backfill \
    --legacy-database-url postgresql+psycopg://user:password@127.0.0.1:5419/stock_db
```

`--legacy-archive` 預設為 `~/GitHubLL/my_stock_project/data/raw/foreign_holding`，
用於依檔案內容分類差異。

## 已知限制與延後的工作

- **下市證券的 MOPS 歷史會繼續消失。** 今後每有一支證券離開 TPEx，MOPS 就會把它從
  過去的日期拿掉。已經儲存的列不受影響（只可附加），但從零重建時只能從
  `insti/qfii` 取得它們的歷史，而 `insti/qfii` 缺陸資上限、異動原因和最近申報日期。
- **消費端如何選擇 TPEx 來源還沒有決定。** 兩個來源都儲存，沒有標準來源政策；依
  CLAUDE.md §30，查詢必須指定來源或取得分開的結果。留給 Step 28 或另一份 ADR。
- **兩個 TPEx 來源的 D 算法不同。** `insti/qfii` 四捨五入、MOPS 截斷，同一支證券同
  一天的 D 可能差 0.01。這是來源的事實，不做調整。
- **`insti/qfii` 2026-04-07 的檔案不可靠。** 見上文；照發布的樣子儲存，對帳中已分類。
- **修正帳本：** ROADMAP Step 20-c 已改為 **MERGED** (#32)。
