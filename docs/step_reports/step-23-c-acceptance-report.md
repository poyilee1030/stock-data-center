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

