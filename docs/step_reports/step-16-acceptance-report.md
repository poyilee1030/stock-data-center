# Step 16 驗收報告

狀態：IN REVIEW

範圍：交易日曆與涵蓋範圍驗證器

由 GitHub pull request [#15](https://github.com/poyilee1030/stock-data-center/pull/15) 交付。
Step 編號和 pull request 編號在這裡分岔，不再對齊：ADR-0020 沒有經過 pull request
直接進了 `main`，所以 Step 16 開的是 #15（ROADMAP §20）。

Schema 影響：`trading_calendar_versions`、它的觀察連結、第十七個
`publication_evidence` 目標，以及 `dataset_expected_coverage`。
Migration：`1a6f3b7c8d24`。PIT 影響：無——在 ADR-0020 完成之前，日曆帶的是
`published_at = NULL` 的 `unknown` 證據。

## 基準，在寫任何程式之前量測

| 量測 | 結果 |
| --- | ---: |
| TWSE `FMTQIK`，2020-01-02 → 2026-09-11 | 1,627 個交易日 |
| 舊系統檔案庫 `daily_quotes/*/sii.csv` | 1,627 個日期 |
| 舊系統檔案庫 `daily_quotes/*/otc.csv` | 1,627 個日期 |
| TWSE 與 TPEx 檔案庫的日期集合 | 完全相同，雙向差異為 0 |
| `FMTQIK` 中的 2024-07-24 / 07-25 | 不存在（颱風休市） |

因此，本 PR 依賴的 TPEx 等價性是**量測出來的，不是假設的**。

## 驗收證據

透過 CLI 實際匯入完整歷史，寫入從零 migrate 的資料庫（81 次請求，間隔 1.2 秒）：

```text
months imported          81
raw artifacts            81
manifests                {'succeeded': 81}
evidence rows            81, of which non-null published_at: 0
coverage_through         2026-09-15
stored trading days      1,627   (2020-01-02 → 2026-09-11)
```

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| 2020-01-02 → 2026-09-11 的日曆與舊系統檔案庫一致 | PASS | 儲存 1,627 天，檔案庫 1,627 個日期；`calendar-only = []`、`archive-only = []`。沒有需要解釋的差異。 |
| 颱風休市以休市呈現 | PASS | 2024-07-24 和 07-25 不在儲存的月份中，`is_trading_day` 對兩者都回傳 `False`。真實抓到的 bytes 是永久 fixture（`tests/fixtures/twse_fmtqik_202407.json`）。 |
| 涵蓋報告把非交易日和缺漏資料分開 | PASS | `CoverageReport.missing` 和 `.non_trading_days` 在結構上互斥：休市日永遠不在預期之中。回歸測試對 07-22..07-26 期間斷言這兩個欄位。 |
| 報告不依賴今天的證券範圍 | PASS | 報告以期間為粒度。一個回歸測試儲存一支證券、產生報告、再為同一天加入兩支證券，並斷言兩份報告相同。 |
| 可以查詢某個 (dataset, period) 範圍的預期涵蓋 | PASS | `dataset_expected_coverage` 是一張表；`ExpectedCoverageService.expected_periods(...)` 直接回答，未宣告的資料集會拋出錯誤，而不是回傳空的預期。 |

## 設計決策（ADR-0021）

**版本以月為粒度。** 休市是從發布清單中*缺席*，所以以日為粒度無法表達被更正的
休市：新增一列可以說「這天確實有開市」，但列永遠不會被刪除，所以「這天其實沒有
開市」就無法表達。以月份作為版本，日期清單改變、業務 hash 改變，就成為該月的新
版本——兩個方向都行得通。ROADMAP 把這張表描述為
`(market, trading date, source, lineage)`；這在查詢層級成立，而儲存粒度依循來源
自己的發布與 revision 單位。

**`coverage_through` 界定一個版本可以回答的範圍。** 月底之前抓取的月份發布的是
不完整的清單，最後一個發布日期之後的日子是未知，而不是休市。仍在進行中的月份只
宣稱來源實際顯示的最後一天——宣稱到*今天*，就會把尚未發布的日子讀成休市。

**超出涵蓋範圍會拋出錯誤。** 在資料中，未匯入的月份和整月休市無法區分，所以回傳
`False` 會默默把「我們從未匯入八月」變成「市場整個八月都沒開」。基於同樣的理由，
已匯入月份之間的缺口會讓涵蓋停在缺口處。

**不寫入任何 TPEx 列。** TPEx 沒有官方的日曆來源，寫入一列就等於捏造資料。TPEx
資料集改為宣告使用 TWSE 日曆，宣告中記錄了背後的量測。

## 驗證

從零 migrate 到 `1a6f3b7c8d24` 的資料庫：

```text
312 passed, 3 skipped, 1 warning
```

`main` 上相同資料庫狀態的基準：在下文 registry 和 downgrade helper 修正之後為 300
passed；這裡新增的 41 個測試就是差異。

每一種儲存層拒絕都檢查過，確認觸發的是**它自己的** constraint，而不是隔壁的：

```text
not first day        ck_trading_calendar_versions_calendar_month_is_first_day
unsorted             ck_trading_calendar_versions_trading_days_sorted_distinct
duplicate            ck_trading_calendar_versions_trading_days_sorted_distinct
empty                ck_trading_calendar_versions_month_has_open_day
coverage too early   ck_trading_calendar_versions_coverage_through_inside_month
coverage outside     ck_trading_calendar_versions_coverage_through_inside_month
```

這項檢查找到了測試中真正的缺口：「在月份之外的日子」被涵蓋界限抓到，而不是被
`trading_days_inside_month` 抓到。測試現在使用月份*之前*的日子，那只有這個
constraint 能拒絕，並以名稱斷言該 constraint。

## 本 PR 被迫做的兩個附帶修正

**Step 14 的儲存契約防護發揮了作用。** 新增這些表立刻讓
`test_every_table_in_the_schema_is_classified_or_explicitly_excluded` 失敗。兩者都
已分類：日曆附上逐欄的來源對應，觀察連結和 `dataset_expected_coverage` 附上理由
排除。

**三個 downgrade 測試把 head revision 寫死**（`7c9e2a4b6d81`），所以每個新
migration 都會弄壞它們。它們現在透過 `conftest.alembic_head()` helper 與 script
head 比較——這個斷言的本意是「被擋下的 downgrade 沒有動到版本」，而不是「head
就是這個字面值」。

## Code review 發現

一次 `/code-review` 提出 12 項發現。12 項在改動任何東西之前都對照執行中的程式
驗證過；沒有一項是誤報。其中十一項在這裡修正，一項排成獨立的 step。

| # | 發現 | 驗證方式 | 處置 |
| --- | --- | --- | --- |
| 1 | `coverage_through` 只要月份存在就推進連續性檢查，所以一個不完整的月份後面接著一個完整的月份時，尚未發布的日子會回答 `False` | 儲存只發布到 11 日的七月，加上完整的八月；`is_trading_day(2024-07-12)` 回傳 `False` | 已修正。檢查在第一個沒有到達自己月底的月份停下。 |
| 2 | `_latest_versions` 依來源分區，卻只依市場過濾，把兩個來源的日曆合在一起 | 閱讀 | 已修正。每次呼叫只讀一個來源，除非指名，否則是標準來源。 |
| 3 | 已觀察期間的查詢沒有來源條件，所以一個市場的列會被算成另一個市場的涵蓋 | 閱讀 | 已修正。宣告帶有 `source`，查詢依它過濾。 |
| 4 | 對 `trading_calendar` 呼叫 `report()` 會當掉：不在 `DATASET_CONTRACTS` 中 | `get_contract('trading_calendar')` 拋出 `UnknownDatasetError` | 已修正。日曆已註冊。 |
| 5 | 預期之外的已觀察期間被過濾掉，而不是呈現出來 | 閱讀 | 已修正。`CoverageReport.unexpected` 回報它們，`is_complete` 也把它們納入考量。 |
| 6 | `non_trading_days` 使用呼叫端未裁切的範圍，而 `expected` 被裁切到宣告的時間窗 | 閱讀 | 已修正。兩者都依循時間窗。 |
| 7 | 每個預期元素都重新建立一次 `set(observed)` | 閱讀 | 已修正。 |
| 8 | `downgrade()` 丟掉 `trading_calendar_versions`，摧毀只可附加的歷史，而不是拒絕 | 閱讀 | 已修正。`P0001` 預檢在修改之前拒絕，符合 Step 12 的慣例，並附回歸測試。 |
| 9 | 證據 hash 包含每個目標欄位，所以新增一個欄位會移動每個資料集的 hash，破壞去重 | 在 PostgreSQL 中計算 `to_jsonb(row) - exclusions`；新欄位確實在其中 | **排為 Step 34。** 不是這裡引入的——Step 8 也做了一樣的事——而且修正它會改變每個領域的 hash 函數，所以需要自己的 step 和回歸測試集。 |
| 10 | 多月份的 CLI 執行只印出最後一個月的 manifest | 閱讀 | 已修正。執行會回報每個月。 |
| 11 | `date.today()` 以 process 的時區判斷一個月份是否已結束 | CLAUDE.md §34 要求 Asia/Taipei | 已修正。 |
| 12 | （與 6 合併） | | 已修正。 |

真正重要的是第 1 項：日曆宣稱了它從未看到的休市，而 ADR-0021 §3 存在的目的就是
防止這種事。原本的不完整月份測試只涵蓋不完整月份在*最後*一個位置的情況，所以看
不到這個 bug——測試是跟著實作長出來的，繼承了它的盲點。
`tests/integration/test_pr16_review_findings.py` 為每項發現保留一個回歸測試。

第 3 項的修正揭露了一件文件宣稱、程式卻從未做到的事：文件說 TPEx 資料集使用 TWSE
日曆，但沒有任何東西記錄這點。宣告現在帶有 `calendar_market`。

## 已確認的範圍排除

- 沒有使用或捏造任何 TPEx 日曆來源。
- 不使用 `holidaySchedule`：它只列出計畫中的休市，2023 年之前什麼都沒回傳，而且
  無法表示颱風休市。
- 這裡只有 `trading_calendar` 宣告它的預期涵蓋。Steps 17–24 與填入它們的 adapter
  一起宣告自己的。
- 不寫入任何 release rule 證據；那是 ADR-0020 下的 Step 15。
