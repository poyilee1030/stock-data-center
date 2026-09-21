# Step 23-c 驗收報告

狀態：IN REVIEW (#41)

範圍：財務報表的第三部分——2020Q1–2026Q2 全量匯入、檔案庫對官方的抽樣關卡、
發布證據，以及舊系統對帳。23-a／23-b 寫入的版本都只帶 `unknown`，Market PIT
看不到任何一份申報；這個 step 把證據補上。新增：

- migration `e1b7d4a92c63`（`mops_t164sb01` 接受 `capture_bound`、
  `legacy_capture_bound`、`release_rule`）
- `archive_capture_bound()`：檔案 mtime → 首見瞬間或 `None`
- `LegacyFinancialFilingArchiveAdapter.capture_bound()`
- `FinancialFilingArchiveImporter._plan_evidence()`
- `FinancialFilingArchiveBackfill` 與 CLI `financial-filing-backfill`
- `scripts/backfill_financial_filings.sh`（逐年並行、可續跑、自我終結）
- `scripts/sample_official_vs_archive.py`（抽樣關卡）
- `scripts/reconcile_financial_statements.py`（舊系統對帳）

Schema 影響：只有 `dataset_sources.accepted_evidence_types` 放寬。**沒有**新增
`dataset_release_rules` 列——理由見下。downgrade 在已存有這三種證據時，於修改前
拒絕（CLAUDE.md §81）。

規模：`src/` +605／−36 行，在 CLAUDE.md §1 的約 800 行門檻內。

## 檔案的 mtime 就是全部的證據

`t164sb01` 不發布任何申報時刻：文件只帶它涵蓋的期間，端點回的是「目前有效」的那一
版。所以財務報表的發布證據全部來自檔案庫檔案的 mtime，而 mtime 說的是兩件事之一
（audit §4.8，2026-09-21 對全部 45,324 份實測）：

| mtime | 份數 | 寫了什麼 |
| --- | ---: | --- |
| 2026-02-21／22／23／24／26 | 36,026 | 一次重抓 2020Q1–2025Q3 |
| 2026-03-04 … 2026-04-01 | 1,653 | 2025Q4，逐日 |
| 2026-04-14 … 2026-05-16 | 1,647 | 2026Q1，逐日 |
| 2026-07-29 … 2026-08-15 | 1,643 | 2026Q2，逐日 |
| 2026-08-01 | 24 | 2026Q1 的 20 份與 2026Q2 的 4 份，22:28–23:59 |
| 2026-08-16 | 293 | 2020Q1 135、2020Q2 156、2026Q2 2 |
| 2026-08-17 | 4,038 | 2020Q1 到 2026Q2 每一季 |

**判準是「那次執行有沒有跨出當時的申報窗口」。** 2026-08-16 同時寫了 2020Q1、2020Q2
與兩份 2026Q2，2026-08-17 寫了每一季——那是批次補抓。每日工作的每一次執行都待在
單一申報窗口內。所以一份檔案證明首見，當且僅當它的季別是 2025Q4 以後**且** mtime
不落在那八個批次日期上：4,943 份（2025Q4 1,653、2026Q1 1,647、2026Q2 1,643）。
其餘 40,381 份證明不了發布時刻，以 `financial_statements_general@1` 解析。

宣告的瞬間是 mtime 本身，不是當日結束。月營收檔案庫只記到日，所以 22-c 取
23:59:59；iXBRL 檔案帶著被寫入的那一秒，是更緊、同樣不早於首見的界線。

## 這個 step 做出的決策

1. **release rule 由 importer 逐份引用，不宣告在來源上。** 與 22-c 同一條理由：
   宣告在 `dataset_sources` 上，官方 importer 寫的每一個版本都會拿到法定時刻，包含
   沒有任何東西證明它準時申報的那些。`financial_statements_general@1` 早在
   migration `3c8e5f1b7a46` 就帶著它的依據（證券交易法 §36）註冊過，這裡只引用。
2. **capture 與 rule 互斥，不是兩條都寫。** capture 本來就勝過 rule（rank 70 對
   40），而 capture 晚於期限時該份是延遲申報、rule 對它被推翻（CLAUDE.md §32）。
   兩條都寫會把被推翻的時刻留在庫裡，等 capture 哪天被取代時變成答案。
3. **證據從檔案讀，不從 fetcher 的狀態讀。** 從 captured checkpoint 續跑的匯入會從
   raw store 讀回 bytes、完全不呼叫 fetcher，`last_mtime` 會是 `None`。證據若因為
   續跑而悄悄退回 rule，就是續跑改寫了歷史（CLAUDE.md §76）。`capture_bound()` 因此
   自己解析檔案並 stat 它；glob 的解析抽成 `resolve_archive_glob()`，與 fetcher 共用
   同一支，兩邊不會對「哪一份檔案回答了這個請求」有不同答案。
4. **2026-08-01 的 4 份 2026Q2 檔案一併歸為批次。** 那次執行在 22:28–23:59 之間同時
   寫了 2026Q1 的 20 份，是一次批次。把這 4 份也當批次，它們改以 2026-08-15 解析，
   比 08-01 的 capture **晚**——偏晚是安全的方向（CLAUDE.md §84 第 1 順位）。
5. **延遲申報的前視風險留著並記錄。** 2025Q4 之前沒有任何首見證據，準時與否無從分辨，
   所以 rule 對真正延遲申報的公司會偏早。ROADMAP §23 的檔案庫方案本來就接受這一點；
   替代方案是 2020Q1–2025Q3 全部留 `unknown`，代價是六年的財務報表在 Market PIT 下
   完全看不到。界線是有的：rule 取的是法規期限，不會更早。記在 audit §7.6。

## 抽樣關卡

`scripts/sample_official_vs_archive.py` 以固定 seed 從 26 季各抽 10 份，把檔案庫那份
與 `t164sb01` 今天回的（以 cp950 解碼）逐字元比較。分類刻意不是「一致／不一致」，
因為差異的意義不同：

- `identical`——官方回應解碼後與檔案庫那份逐字元相同。
- `same_filing`——兩份有差異，但三張報表的列在 `mops-filing-revision:v1` 下指紋相同，
  是同一個 source revision；差在三張報表以外，不是 v1 的資料。
- `corrected_since_capture`——報表列不同，MOPS 之後服務了更正後的申報。這是真的後續
  revision、不是檔案庫壞掉：匯入檔案庫那份存的是當時申報的內容，更正由 Step 27 的
  前向抓取以它自己的版本與抓取證據存入。
- `no_longer_served`——兩個 `REPORT_ID` 都回 `檔案不存在!`。文件寫進檔案庫時存在、
  端點現在不再提供，這正是留著檔案庫的理由。
- `unexplained`——官方回應讀不出來，或回的是另一家公司。只有這一類算進關卡。

2026-09-21 以 `--seed 23 --per-quarter 10` 實跑：

| 判定 | 份數 |
| --- | ---: |
| `identical`，逐字元相同 | 249 |
| `unmappable_source_byte` | 2 |
| 在 v1 邊界擋掉、沒有去要 | 9 |
| `corrected_since_capture` | 0 |
| `no_longer_served` | 0 |
| `same_filing`（差在三張報表以外） | 0 |

251 份可比對的文件中 249 份與今天的官方回應逐位元組相同；抽樣裡沒有任何一份在
檔案庫寫入之後被更正過，也沒有任何一份停止提供。`unexplained` 率 0.797%，門檻 1%，
**通過**。

### 關卡找到的來源事實：有些官方文件帶著沒有 codec 對映的位元組

兩個例外是 6470 2025Q1 與 5315 2026Q1，失敗方式相同：官方回應含 `0x84 0x50`，
落在 Big5 使用者造字區，`cp950` 與 `big5hkscs` 都不對映，整份文件解不出來、parser
fail closed。舊爬蟲當時是以 replacement 解碼的，所以檔案庫那份在同一位置是 U+FFFD
加上 `P`——這也是這兩份永遠不可能判為 `identical` 的原因。

全檔案庫實測：**45,324 份中有 717 份帶 U+FFFD、共 2,994 處，沒有任何一處落在三張
報表內**，全部在敘述文字裡，也沒有任何一處在帶 `ix:nonFraction` 的儲存格。所以沒有
任何已儲存的值依賴這個字元，本 step 的 backfill 不受影響。

影響的是**官方**路徑而不是這次 backfill：重新抓取那些文件會 quarantine 成
`unreadable_document`。那是 fail-closed 而且正確——猜一個使用者造字等於發明來源
內容——但 Step 27 前向抓取要處理它，已記在 ROADMAP Step 27。

## 全量 backfill

`scripts/backfill_financial_filings.sh`，七個逐年的 process 並行，各自可續跑：

```text
2020Q1..2020Q4  documents=6559 imported=6126 quarantined=433 failed=0 facts=2331751
2021Q1..2021Q4  documents=6722 imported=6250 quarantined=472 failed=0 facts=2377049
2022Q1..2022Q4  documents=6871 imported=6400 quarantined=471 failed=0 facts=2425178
2023Q1..2023Q4  documents=7030 imported=6560 quarantined=470 failed=0 facts=2479249
2024Q1..2024Q4  documents=7180 imported=6780 quarantined=400 failed=0 facts=2546918
2025Q1..2025Q4  documents=7302 imported=7021 quarantined=281 failed=0 facts=2633154
2026Q1..2026Q2  documents=3660 imported=3613 quarantined= 47 failed=0 facts=1387060
--------------------------------------------------------------------------------
                documents=45324 imported=42750 quarantined=2574 failed=0
                facts=16180359
```

`facts=16,180,359` 與 23-b 從檔案庫掃描算出的預測**完全相同**（audit §4.8）。

擋在邊界的 2,574 份逐份記名在 manifest 裡：

| 原因 | 份數 |
| --- | ---: |
| `outside_v1_universe`（興櫃、公開發行、非公開發行） | 1,667 |
| `financial_industry_issuer` | 904 |
| `unreadable_document` | 3 |

那 3 份是 23-a 已知的 2855 統一證券——它們引用了文件裡不存在的 context，fail closed。
904 + 3 = 907，正是 23-a 數到的金融業份數。

`business_versions_created` 40,988 加 `deduplicated` 1,762 等於 42,750：重複的那
1,762 份是 23-b 已經匯入過的 2025Q1 全季與零星測試，重跑沒有產生假版本。

## 發布證據

42,750 個版本，每一個都有一筆帶 `published_at` 的證據（**0** 個沒有）：

| 證據型別 | 版本數 |
| --- | ---: |
| `release_rule`（`financial_statements_general@1`） | 37,855 |
| `legacy_capture_bound`（檔案 mtime） | 4,895 |
| 23-b 留下的 `official`／`unknown`（append-only，被上面兩者壓過） | 1,762 |

4,895 比檔案庫的 4,943 份少 48，差額是 2025Q4 起被邊界擋掉的金融業與非 sii／otc
文件。

`scripts/verify_financial_filing_evidence.py` 不信任寫入它的那次匯入，而是逐個版本
回到檔案庫、重新套用 `archive_capture_bound`、再比對資料庫裡的那一筆：

```text
legacy_capture_bound  4,895   型別、時刻（等於檔案 mtime）、evidence_source 全部相符
release_rule         37,855   時刻等於該季 rule 解出的值，來源字串是 rule_id@version
disagrees                 0
no_archive_file           0
```

## 舊系統對帳

`scripts/reconcile_financial_statements.py`，2020Q1–2026Q2 逐季走完，結束碼 0。
join 就是 ROADMAP §23 要的那一個：會計科目代碼 ↔ concept QName。

```text
我們的申報   42,750
舊系統的申報 45,322
兩邊都有     42,748
只有我們有        2
只有舊系統有  2,574
```

逐值比對：

| 類別 | 筆數 |
| --- | ---: |
| `identical` | **7,353,457** |
| 其中是舊系統消費端會再乘 1,000 的股數 | 105,205 |
| `value_differs` | **0** |
| `absent_from_ours:filing_outside_v1_scope` | 305,787 |
| `absent_from_ours:legacy_derived_the_q4_single_quarter` | 352,923 |
| `absent_from_legacy:outside_legacy_codebook` | 520 |
| `absent_from_legacy:filing_absent_from_legacy` | 358 |

**沒有任何一筆值不同。** 四個未配對的類別各自有來源上的解釋：

1. **`filing_outside_v1_scope` 305,787 筆**——舊系統存了 v1 排除的申報（金融業、
   非 sii／otc），那些申報的每一列都在這裡。
2. **`legacy_derived_the_q4_single_quarter` 352,923 筆**——**Q4 的單季欄位不在文件
   裡**。年報印的是全年與去年同期；舊系統的 10–12 月數字是累計減去前一季，那是衍生
   資料（Step 26），不是來源印出的值。實測支持：我們 Q4 的綜合損益事實只有
   `period_start = 1/1` 一種（352,923 筆），舊系統 Q4 有 373,371 筆 `quarter` 列。
3. **`outside_legacy_codebook` 520 筆**——`AA0000`、`AB0000`、`AC0100`–`AC0500`
   這些現金流量表小計，舊系統的 importer 以它 1,748 個代碼的 `xbrl_codebook` 過濾掉了
   （23-b 已量過：1,040 列、332 份文件）。
4. **`filing_absent_from_legacy` 358 筆**——只有兩份申報：**1519 2021Q2** 與
   **6243 2023Q3**，舊系統整份都沒有。1519 2021Q2 正是 header 值被斷行的那一份
   （`Consolidated \r\nreport`，audit §4.8），舊系統的 regex 轉換器讀不了它。這兩份
   是舊系統的缺口，不是我們的。

`quarterly_reports_xbrl`（舊系統在三張表之上建的季表）：

| 欄位 | identical | both_absent |
| --- | ---: | ---: |
| `revenue_acc` | 42,641 | 107 |
| `op_income_acc` | 42,644 | 104 |
| `non_op_income_acc` | 42,748 | 0 |
| `pretax_income_acc` | 42,644 | 104 |
| `net_income_acc`（合併 8610／個體 8200） | 42,644 | 104 |
| `eps_acc` | 42,644 | 104 |
| `capital`（資產負債表 3110） | 42,748 | 0 |

`report_category` 每一份都相符（不相符會單獨計數，結果是 0）。`_q`、`_ly`、`_yoy`
與四個財務比率是衍生欄位——`_q` 是累計相減、`_yoy` 是比率、比率是公式——屬於 Step 26，
不在這裡對帳。

**單位是逐單位的，不是逐表的。** 舊系統存的是印出來的數字，仟元乘數留給它的消費端，
所以我們的 `iso4217:TWD` 是舊系統的 ×1,000，而 `xbrli:shares` 與 EPS 的
`iso4217:TWD/xbrli:shares` 照印出來存。23-b 那個 413 列的 fixture 在這裡以具名類別
留著：105,205 筆相符的事實是股數，舊系統的**消費端**照樣把它們乘 1,000，那是下游的
差異、不是已儲存的差異。

## 驗收

| 標準 | 結果 | 證據 |
| --- | --- | --- |
| 舊系統 `*_xbrl` 和 `quarterly_reports_xbrl` 的值透過會計科目代碼 ↔ concept QName 對帳 | **PASS** | 7,353,457 筆逐值相符、`value_differs` **0**；`quarterly_reports_xbrl` 七個來源欄位各 42,641–42,748 筆相符、`report_category` 全數相符。四個未配對類別逐一有來源解釋（見上）。`scripts/reconcile_financial_statements.py` 結束碼 0 |
| 檔案庫與官方的抽樣比較已執行，不一致率記錄在 audit | **PASS** | 26 季各 10 份、seed 23：251 份可比對中 249 份逐字元相同，0 份被更正、0 份停止提供；`unexplained` 率 0.797% < 門檻 1%。兩個例外是 `0x84 0x50` 使用者造字，記在 audit §4.8 與 ROADMAP Step 27 |
| 2025Q4 起，每日工作的抓取在 Market PIT 下的解析時間不早於其舊系統抓取界限 | **PASS** | 4,895 個版本帶 `legacy_capture_bound`；`scripts/verify_financial_filing_evidence.py` 逐個回到檔案庫重新推導，型別、時刻與來源字串**全數相符、0 不符**。真實資料 spot check：1232 2025Q4 在 capture（2026-03-04 00:47:38 +08）可見、前一秒不可見 |
| 2020Q1–2026Q2 全量匯入 | **PASS** | 45,324 份文件、42,750 份匯入、**0 失敗**、16,180,359 筆事實，與 23-b 的預測完全相同；2,574 份在邊界擋掉並逐份記名 |

其他量測：

| 量測 | 值 |
| --- | ---: |
| `financial_filing_versions` | 42,750 |
| `financial_facts` | 16,180,359 |
| 沒有帶 `published_at` 證據的版本 | **0** |
| 重複匯入造成的假版本 | **0**（1,762 份去重） |
| 匯入失敗 | **0** |

真實資料 PIT spot check：

```text
1232 2025Q4  capture 2026-03-04 00:47:38 +08   該時刻可見、前一秒不可見
1101 2020Q2  rule    2020-08-17 23:59:59 +08   該時刻可見、前一秒不可見
```

2020Q2 的法定期限 2020-08-15 是星期六，rule 的營業日順延把它移到 8/17 星期一，
這是 `financial_statements_general@1` 自己解出來的。

## 測試

先寫會紅的測試再實作：`tests/unit/test_step23c_archive_evidence.py` 13 條（mtime →
首見瞬間／`None` 的規則、八個批次日期、時區、rule 版本），
`tests/integration/test_step23c_financial_filing_backfill.py` 13 條（兩種證據路徑、
Market PIT 在該瞬間可見／前一秒不可見、System PIT 仍看得到、重跑不產生版本與證據、
backfill 的走訪／排除／續跑／缺季報錯、CLI manifest、來源 allowlist）。實作前全部跑紅過。

兩個例外，據實說明：

- CLI 那一條（`test_the_cli_walks_the_archive_and_reports_its_manifest`）是在接好
  CLI **之後**寫的，沒有紅過。
- `tests/unit/test_step23c_reconciliation_mapping.py` 10 條是**跑出來才發現的 bug 的
  回歸**，不是先寫的：對帳腳本第一次跑真實 `stock_db` 時，Q1 的單一 duration 只對到
  `accumulated`，讓舊系統 57,000 列被報成缺漏；改成一筆事實對兩個標籤之後，現金流量表
  又多出 84,000 列不存在的 `quarter` key。兩者都由實跑發現，測試把結果釘住。

```text
tests/unit/test_step23c_archive_evidence.py              13 passed
tests/unit/test_step23c_reconciliation_mapping.py        10 passed
tests/integration/test_step23c_financial_filing_backfill.py  13 passed
```

```text
全套                                                     1,110 passed, 3 skipped
alembic upgrade head / downgrade -1 / upgrade head        pass（空白資料庫）
alembic check                                            No new upgrade operations detected
```

migration 的 downgrade 守門在真實資料上實測：對已存 42,750 筆證據的
`stockdc_backfill` 執行 `alembic downgrade -1`，在修改任何東西之前就拒絕
（`financial_filing holds 42750 archive evidence rows`），head 仍是 `e1b7d4a92c63`。

23-b 有兩條測試因為本 step 而要改：2025Q1 的檔案庫匯入現在會以法定期限解析，而 rule
的營業日順延要問交易日曆，日曆在未匯入的範圍會拒答（Step 15-b）。兩條測試因此先存入
2025-05 的日曆。這是真的新依賴，記在下面的限制裡。日曆的建立抽成
`tests/conftest.py` 的 `store_trading_calendar`。

## 已知限制與延後的工作

- **2025Q4 之前的延遲申報會偏早。** 那段沒有任何首見證據，準時與否無從分辨，rule 取
  法定期限。ROADMAP §23 的檔案庫方案接受這一點；替代方案是六年的財務報表在 Market PIT
  下完全看不到。記在 audit §7.6。
- **官方路徑讀不了帶 Big5 使用者造字的文件。** 抽樣 251 份中 2 份，全檔案庫 717 份帶
  U+FFFD 但沒有一處在三張報表內。fail closed 是正確的，處理方式留給 Step 27。
- **復原 2025Q4 之前修正前的原始申報**：ROADMAP 明列的範圍外。端點回的是目前有效的
  那一版，檔案庫存的也是 2026 年重抓的結果。
- **Q4 單季**是衍生資料（累計相減），Step 26；本 step 只存文件印出來的值。
- **權益變動表、附註、附表與敘述區塊**要不要存：ROADMAP 最後再決定（owner）。
- 本 step 沒有 `docs/stepNN.html` 教材頁，冷讀 N/A。

## 這次踩到的坑

**backfill 執行中不要動工作樹。** `RawFirstImporter.run` 是**逐個資源**問 git 的，
而 manifest 的 `git_commit` 不一致會被當成「configuration 改變」拒絕。我在 45,324 份
的走訪跑到一半時 commit 了一次，那一次 backfill 的 manifest 因此分成兩個 commit 值，
整段變成無法續跑（這次跑完了，所以沒有實際損失）。

**原本以為**每個 backfill runner 都已經把 git commit 取一次往下傳——
`WholeMarketDailyBackfill` 確實收 `git_commit` 參數，但 CLI 沒有傳，所以實際上一直是
逐份重新問。修法是 `FinancialFilingArchiveBackfill.run` 自己在走訪開始時取一次
（`current_git_commit()`，順手從 `_git_commit` 改成公開名稱並寫清楚為什麼要公開），
並加一條回歸：走訪中途 git 的答案改變，所有 manifest 仍然只帶第一個值。

其餘的 backfill 路徑沒有改——它們的窗口短得多，而且本 step 不擁有它們（CLAUDE.md §67）。
