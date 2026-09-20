# Step 22-c 驗收報告

狀態：IN REVIEW (#38)

範圍：月營收的第三部分，發布證據。22-a／22-b 寫入的版本都只帶 `unknown`，Market PIT
看不到任何一列；這個 step 從舊系統 `market.csv` 把證據補上。新增：

- migration `c8f1a63d5b02`（兩個來源接受 `press_report_bound`、`legacy_capture_bound`、
  `release_rule`、`capture_bound`）
- `LegacyMonthlyRevenueArchiveAdapter`：讀一個月份的 `market.csv`，一個 instance 一個市場
- `MonthlyRevenueArchiveImporter`：以 `legacy_archive` 來源走 raw-first 生命週期
- `evidence/plan.py` 的 `archive_evidence_plan` 與 `evidence/policy.py` 的 `ArchivePlanner`
- CLI `monthly-revenue-archive`
- `scripts/verify_monthly_revenue_evidence.py`

Schema 影響：只有 `dataset_sources.accepted_evidence_types` 放寬。**沒有**新增
`dataset_release_rules` 列——原因見下面的決策。downgrade 在已存有這四種證據時，
於修改前拒絕（§81）。

PIT 影響：150,757 個版本中的 140,345 個現在在 Market PIT 下可見。其餘 10,412 個
維持看不到，原因逐項量化在下面。

規模：`src/` +1,015／−18 行，**超過** `CLAUDE.md` §1 約 800 行的拆分門檻。分布：

```text
ingestion/monthly_revenue_archive.py      +460   importer
adapters/monthly_revenue_archive.py       +217   adapter
ingestion/cli.py                          +122   子指令
evidence/policy.py                         +90   ArchivePlanner
evidence/plan.py                           +67   archive_evidence_plan
ingestion/models.py / adapters/__init__.py +59   契約與匯出
```

沒有拆，理由是找不到一條「每一半自己成立」的接縫：adapter 單獨合併是一支沒有消費者的
parser，而 importer、evidence plan、policy、migration 四者是同一份契約——
`archive_evidence_plan` 決定可以宣告什麼、`ArchivePlanner` 決定來源接不接受、migration
決定 allowlist、importer 決定哪個窗口——任何一刀都會讓半份契約先進 main（§1 明文禁止的
那種拆法）。請 reviewer 覆核這個判斷；若認為應該拆，我可以把
「adapter + models + unit test」先切成 22-c-1。

另加一個 migration、一支驗證腳本、三個 fixture 與兩個測試檔。

## 開工時的兩個 owner 決策（2026-09-20）

1. **只在 `_1` 頁出現的 KY／外國發行公司（上市 95 家、上櫃 30 家，含 DR）留 `unknown`。**
   舊系統的爬蟲把網址寫死成 `_0`，檔案裡沒有它們任何一列，所以沒有東西能證明它們
   何時公開。備選方案是給它們法定 10 日的 release rule，但 2026M02 起的首見資料顯示
   每個月有 13–311 家國內公司在 10 日之後才首次出現，外國發行公司不會更早——那會是
   真的前視偏差。**晚而看不到是安全的，早是不安全的**（CLAUDE.md §84 第 1 順位）。

   這個決策直接決定了實作：release rule **不宣告在來源上**。宣告在來源上，官方
   importer 寫的每一個版本都會拿到法定時刻，包含舊系統從未抓過的那些列。改成由
   archive importer 對「復原日期正好落在法定 10 日」的列逐列引用
   `monthly_revenue_statutory@1`。

2. **2020M01–2026M01 之間 166 列已證實被更正過的列，照樣附上復原的公告日期。**
   那段我們手上只有更正後的值（還原首次發布值是 ROADMAP 明列的範圍外），所以公告日
   會落在更正後的值上，使它比實際更正更早可見。影響已量化：140,963 列中的 166 列
   （0.1%）。owner 選擇附上並記錄，而不是讓這些列在 Market PIT 下消失。

## 兩個窗口

| 窗口 | 檔案裡是什麼 | 宣告什麼 | 列數（上市／上櫃） |
| --- | --- | --- | ---: |
| 2020M01–2026M01 | 復原的公告日期，旁邊是今天的（已更正）值 | 該日結束時的 `press_report_bound` | 47,189／40,365 |
| 同上，日期仍是法定 10 日 | 無法與 fallback 區分 | release rule（10 日遇假日順延） | 21,418／18,507 |
| 2026M02 起 | 舊系統 22:45 job 的首見日期與它當時看到的值 | 該值自己的版本 + `legacy_capture_bound` | 6,888／5,978 |

時刻一律取該日在市場時區的結束（23:59:59）：檔案只記到日，22:45 開跑的那次執行
結束在那之後，新聞報導也只有日期。**偏晚是安全的**（CLAUDE.md §32）。

## 驗收證據

匯入指令（`stockdc_backfill`，80 個月 × 2 個市場 = 160 次）與驗證指令：

```bash
python -m stock_data_center.ingestion.cli --database-url … --purpose gap_fill \
    monthly-revenue-archive --source mops_t21sc03_sii --period 2020-01 --through 2026-08
python scripts/verify_monthly_revenue_evidence.py --database-url …
```

驗證腳本結束碼 0：每一列的宣告都與它來自的 archive 列相符，抽樣 400 列端到端解析
也與證據時刻一致。

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| 2026M02 起，每個首次看到的列在 Market PIT 下的解析時間，不早於其舊系統 22:45 執行的結束時間 | PASS | 12,866 列帶 `legacy_capture_bound`，驗證腳本逐列比對：型別、時刻（該首見日 23:59:59 +08）、以及「不早於當日 22:45」三項皆無失敗。整合測試 `test_a_first_captured_value_is_its_own_version_with_a_capture_bound`（6441 廣錠 2026M06，13,094 千元／2026-07-06）。 |
| 重新抓取顯示被更正的列，在更正被抓到之前解析為首次抓取的值 | PASS | `test_the_correction_stays_invisible_until_something_captures_it`：Market PIT 先答 13,094；一次 `correction_check` 抓到再一次更正後，從那個時刻起答新值，而在那之前仍答 13,094。真實資料上，2026M02 起被更正的 1,088 個版本在 Market PIT 下看不到，Market PIT 答的是舊系統首次抓到的值。 |
| 2020M01-2026M01 期間，`publish_time` 不是次月 10 日的列，在該日結束時解析 | PASS | 87,554 列帶 `press_report_bound`，時刻全部等於該日 23:59:59 +08；抽樣 400 列在該時刻可見、在前一秒不可見。 |
| 落在 10 日的列以 release rule 解析，永遠不早於 release rule（10 日落在週末時順延到下一個營業日） | PASS | 39,925 列帶 `release_rule`，時刻全部等於 `monthly_revenue_statutory@1` 對該月解出的值，且全部不早於 10 日結束。整合測試 `test_a_row_left_on_the_statutory_tenth_resolves_by_the_rule`：2021M03 的 10 日（2021-04-10）是週六，解析到 2021-04-12 結束，而 4/10 當下看不到。 |

其他量化結果：

- **可見度：** 150,757 個版本中 140,345 個在 Market PIT 下可見。看不到的 10,412 個中，
  8,952 個屬於 archive 完全沒有的發行公司（`_1` 頁的 95+30 家），1,460 個是 archive
  有該公司、但該列是更正後版本（其中 1,088 個落在 2026M02 起的窗口）。
- **沒有為 archive 發明涵蓋：** 618 個 archive 列（上市 230、上櫃 388）在官方頁面上
  已經沒有對應版本（22-b 的 survivorship），逐列記在 manifest 的
  `skipped_security_codes`，不建立任何 security 或版本。
- **KY：** 驗證腳本確認這 125 家公司身上的非 `official` 證據列數為 0。

## 這個 step 中做出的實作決策

1. **release rule 由 importer 逐列引用，不宣告在來源上。** 見上面的決策 1。
2. **舊系統自己解碼壞掉的備註不算新版本。** 22-b 量過舊系統的備註在發行公司沒有改寫
   時會怎麼壞（收斂空白、U+FFFD、`A1 45` 讀成 `•`、字面 `NA` 變 NULL）。2026M02 起
   只差在這種形狀的列，會沿用官方版本的備註而去重，證據掛在官方版本上。否則會因為
   我們自己的抓取瑕疵分出一個版本，而且 Market PIT 答的會是壞掉的字。全窗口 15 列。
3. **不從 archive 建立 security 或版本。** archive 帶著官方頁面已經不列的發行公司；
   從檔案生出身分，等於用舊系統的檔案發明涵蓋。這些列只計數、列名。
4. **rule 只在該月真的有列要用時才解析。** 解析會問交易日曆，而日曆在窗口外會拒答；
   對一個沒有任何列要引用 rule 的月份去問，會讓一次什麼都沒宣告的匯入失敗。

## 驗證

從零 migrate 的資料庫：

```text
984 passed, 3 skipped, 2 warnings in 419.05s
```

其中 33 條是這個 step 新增的：16 個 integration、17 個 unit（含 code review 後
補的 8 條回歸）。

每個測試如何確認先失敗：

- **Unit（10 個）：** adapter 存在之前整個檔案在收集階段失敗；之後逐項確認——市場過濾
  未實作時兩條失敗、千元換算未實作時一條失敗、`publish_time` 尚未解析成日期時一條失敗、
  三條錯誤路徑（未知 header、v1 範圍外的市場、壞掉的日期）在收緊解析之前失敗。
- **Integration（15 個）：** importer 存在之前整個檔案在收集階段失敗；之後逐項確認——
  窗口判斷寫反時 5 條失敗、備註修復未實作時 2 條失敗、`skipped_security_codes` 未實作
  時 1 條失敗、CLI 子指令不存在時 1 條以 argparse 錯誤失敗、migration 未加入 allowlist
  時 `UnacceptedEvidenceTypeError` 讓 4 條失敗、downgrade guard 未加時 1 條失敗。

`ruff check` 對新增與改動的檔案沒有回報問題。

## Code review（#38）

review 列出 6 項（3 medium、3 low）。逐項回核後全部成立，全部已修，並各留一條回歸：

1. **`_existing_evidence` 只讀官方版本的 id，2026M02 起的窗口會把重跑的去重證據算成新建。**
   成立且只在這個 step 新增的窗口上成立：被更正列的證據掛在 `append_revenue` 回傳的版本上，
   那個版本不在 `held` 裡。manifest 因此在重跑時報 `publication_evidence_created > 0`，
   與 §76／§78 的冪等陳述矛盾。改成兩段式：先決定每一列的目標版本（必要時寫入），
   再一次讀出這些版本既有的證據，最後才寫。回歸
   `test_rerunning_a_first_capture_month_reports_no_new_evidence` 在修正前紅（`assert 1 == 0`）。
   真實資料重跑 2026M02–M08 共 14 次匯入：新版本 0、新證據 0。
2. **`same_published_note` 只要 legacy 備註含 U+FFFD 就回 True。** 成立，是這個 step 最該修的
   一項：發行公司改寫過、而 legacy 的副本剛好也有亂碼位元組時，會把今天的字裝進一個
   帶 `legacy_capture_bound`（時間是 legacy 擷取日）的版本——正是這個 step 要防的 look-ahead。
   改成用 `difflib` 逐段比對：兩邊不一致的每一段，legacy 那側都必須含 U+FFFD，未被替換的
   片段必須照順序相符。新增 5 條 unit test，其中「改寫＋亂碼」那條修正前回 True。
   真實資料以新規則重跑：9 列（上市 8、上櫃 1）仍判為同一段文字，沒有任何新版本，
   代表這 9 列確實都是解碼瑕疵而不是改寫。
3. **`_amount` 接受 `NaN`／`Infinity`。** 成立：`Decimal("NaN")` 解得動，PostgreSQL 的
   NUMERIC 存得下，而 legacy 檔就是 Python scraper 產的。改成與官方 adapter 一樣先過
   regex `fullmatch`。回歸涵蓋 `NaN`、`Infinity`、`1e9`。
4. **驗證腳本的 `quality_rank DESC` 在 PostgreSQL 是 NULLS FIRST。** 成立但**目前是潛伏的**：
   每個版本都帶著 22-a／22-b 寫的 `official`（rank 0）列，所以 LEFT JOIN 實際上不會產生
   NULL rank——全庫查過，沒有任何版本是零證據列。已加 `NULLS LAST`；修正前後的計數完全相同
   （見下），證實這一項沒有汙染過驗收數字。
5. **`earlier_than_the_2245_run` 是走不到的 `elif`。** 成立：它只在時刻已經等於當日結束時才
   會跑，不可能更早。驗收報告宣稱量測了這個不變式，所以改成獨立檢查（「不早於 rule」那條
   同樣改成獨立）。修正後重跑仍然 0 失敗。
6. **`_captured_on` 忽略第 8 字元之後的內容。** 成立：`20210409T00:00:00` 會被靜默接受。
   改成先要求 `\d{8}` 全比對。

修正後重跑驗證腳本：結束碼仍為 0，四類的列數與修正前**完全相同**
（上市 21,418／47,189／6,888／230，上櫃 18,507／40,365／5,978／388），
抽樣 400 列仍全數通過。

## 踩到的坑

- **每列一次 COUNT，跟每列一次解 rule。** 第一版對每個版本跑兩次 `count(*)` 來算新增
  的證據數，又對每一列重新解一次 release rule（會查交易日曆）。一個月份 900 列跑了
  11 分鐘還沒完。改成「整個月份先讀一次既有證據 id」＋「rule 每月解一次」之後，
  同一個月份 1.9 秒。
- **rule 解析會要求交易日曆涵蓋該月，即使沒有任何列要用它。** 把「先解 rule」提前之後，
  沒有任何 10 日列的月份也會去問日曆，於是整合測試整批紅掉。改成只有該月真的有列
  要引用 rule 時才解析。
- **原本以為 System PIT 會答今天頁面上的值。** 2026M02 起被更正的列，archive 版本是
  最後被 ingest 的，所以 System PIT（依 `ingested_at` 取最新）答的是舊系統首次抓到的
  值。這是照實發生的事——`ingested_at` 由儲存產生，archive 確實是今天才讀的
  （CLAUDE.md §23、§74）——已記錄在 audit §7.5，沒有繞過。
- **`--import-id` 重用會被 git dirty 擋下，這次換個地方再踩一次。** 21-b 記過的同一個
  坑：被中斷的執行留下 `running` manifest，工作區變髒之後用同一個衍生 id 重跑會被
  「import_id cannot be reused with changed configuration」擋住。順手把 CLI 的月份迴圈
  改成「一個月份失敗只記錄、不中斷整趟」，與其他 backfill 一致。
- **重用的 `stockdc` 測試庫又讓計數全表的測試假失敗。** 第三次了；重建即恢復。

## 已知限制與延後的工作

- **2026M02 起被更正的 1,088 個版本**在 Market PIT 下看不到，要等 Step 27 前向抓取
  以 `correction_check` 抓到它們才會有 `capture_bound`。22-b 以 `gap_fill` 匯入官方頁面，
  而 `gap_fill` 依 ADR-0020 §2 不得宣告 capture bound，所以這個證明只能由之後的執行提供。
- **`_1` 頁的 125 家發行公司**（8,952 個版本）同樣等 Step 27。
- **166 列的前視偏差**依 owner 決定保留並記錄，不修。
- **`pub`／`rotc` 的 618 個 archive 列**不在 v1 範圍內，不會補。
