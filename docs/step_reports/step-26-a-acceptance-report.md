# Step 26-a 驗收報告

狀態：IN REVIEW (#44)

範圍：標準衍生資料的第一部分——derivation 服務基礎，以及第一個定義
`technical_indicators:v1`。依 ROADMAP §17，衍生資料**即時計算、不實體化**。新增：

- `src/stock_data_center/derived/`：`definitions.py`（定義與冪等註冊，
  `storage_strategy = virtual`）、`indicators.py`（純函數計算器）、`service.py`
  （`compute` 單一 PIT context、`rolling` 滾動 as-of 序列）
- `src/stock_data_center/pit/history.py` 與 `PITResolver.market_history`：一次讀進
  一段 key 範圍的所有版本與證據，在記憶體中以與 `resolve` 相同的規則選出可見版本
- `ReleaseRuleService.instants_for`：一條 rule 讀一次，對多個期間求值
- migration `c7f4a1e08b23`（`dataset_catalog` 的 `technical_indicators` 列，
  以及 `publication_evidence` 依證據目標查詢的索引）
- `scripts/reconcile_technical_indicators.py`
- fixture `tests/fixtures/step26a_technical_indicators_legacy.json`

### 第一版曾經實體化，review 前改掉

第一版照 §17 當時的文字，把滾動序列寫進 `derived_metric_versions`：2,582 支證券、
75,990,376 列、約 56 GB，是它的輸入 `daily_price`（2 GB）的 28 倍。長格式每個
metric-day 約 740 bytes，其中約 98% 是逐列重複的 lineage（`input_dataset_identity`、
兩個 64 字元 hash、三個 PIT 時間戳、run id），真正的值只有一個 `numeric`。

這些值是已版本化輸入的確定性函數，存下來不增加任何可稽核的事實，所以 §17 改成
預設即時計算（2026-09-23 owner 決定），26-a 改寫成不寫任何結果列。
`stockdc_backfill` 裡第一版寫入的定義、2,630 筆計算執行與 7,600 萬列已清除，
資料庫從 94 GB 降到 38 GB。`derived_metric_versions` 的儲存契約保留，留給日後
經量測證明需要實體化的指標。

## Step 26 為什麼拆成五個

七個 derivation 各自要註冊定義、實作計算、提供滾動序列、證明它與單一 PIT context
的計算一致，再與舊系統逐欄對帳。舊系統的計算程式本身 1,791 行。拆的縫是輸入領域，
每一段自己就是完整的契約（ROADMAP §24）。

規模：`src/` 約 +1,140 行（含 PIT 套件的批次路徑），超過 CLAUDE.md §1 的約 800 行
門檻。再往下拆會把 `market_history` 與使用它的 service 分開——批次路徑的正確性
只能由「滾動序列等於逐點計算」證明，先合併其中一半就是「合併了半個契約」。
所以這一段維持完整，超出的部分記在這裡。

```text
service.py       366   compute、rolling、分段
indicators.py    244   純函數公式
definitions.py   146   定義與冪等註冊
pit/history.py   121   一段 key 範圍的可見歷史
pit/evidence.py  +45   批次證據查詢，與單筆共用選擇規則
pit/resolver.py  +62   market_history
release_rules.py +26   instants_for
```

## 移植的判準：對照舊系統自己的輸出，不是對照 pandas 文件

計算器是純 Python，沒有 pandas。要證明它是**同一個**公式，唯一有力的證據是舊系統
自己算出來的數字，所以 fixture 取自 legacy `stock_db`：`daily_quotes` 給輸入，
`technical_indicators` 給預期輸出，視窗從 2020-01-02（legacy 序列的真正起點）開始，
指數移動平均的暖機因此完全對齊。

移植前先對 2330 與 1418 的**全部 6.7 年**做過一次比對：

```text
2330  1,633 天 × 14 欄   最大差異  macd_dea 1.28e-09
1418  1,626 天 × 14 欄   最大差異  macd_dea 3.29e-11
```

差異全部是浮點結合順序，沒有一欄的語意不同。凍結成 fixture 的是其中
2020-01-02 → 2021-06-30 的 361 天（2330）與 356 天（1418），涵蓋 ma240 有值的
區間。

兩支證券各自證明一類邊界：

- **2330** 是完整的一般情形，且活得夠久讓 ma240／vma240 有值。
- **1418** 有 legacy 全庫 2,441 筆「有量但三價皆空」的列中的一部分，
  也在 2020-02-27 收出一個九日高低相等的視窗。前者證明 rolling 視窗碰到未發布
  的價格要留空而不是把缺口接起來，後者證明 RSV 的 0/0 要落在中性的 50。

## 這個 step 做出的決策

### 滾動序列的截止點來自輸入的 release rule

ROADMAP §17 只說「該日期截止點時可見的輸入」。這裡把它定死：
`cutoff(D)` 是該 derivation 所有必要輸入中，週期 D 最晚的 release 時刻，而它從
`dataset_release_rules` 查出來，不是寫死的常數。`daily_price` 遵循
`exchange_daily_settled@1`，所以 `cutoff(D) = D+1 03:00+08:00`。

把截止點取在 D 當天結束會讓 D 自己的收盤價不可見，整條序列系統性落後一天。

### 兩條 PIT 軸不是同一條

`information_as_of` 隨觀察日移動；`knowledge_as_of` 是整個執行共用的。第一版把
兩者都綁在觀察日上，結果是**沒有任何一列可見**：證據的 `recorded_at` 是
2026 年，而 2020 年的 knowledge cutoff 看不到它。這正是 CLAUDE.md §15 把兩條軸
分開的理由，也是本 step 第一個被測試抓到的錯誤。

### 一次讀進整段歷史，再在記憶體中解析

逐日呼叫 resolver 是 O(N²)：2330 一支就是 260 萬次 resolve。改成一次讀進：

版本的 authoritative 證據只取決於 knowledge cutoff 與來源政策，**與
`information_as_of` 無關**。所以在固定的 `knowledge_as_of` 下，每個版本的證據只要
決定一次；任何 information cutoff 的可見版本，都是對記憶體中資料的選擇。
`market_history` 用兩個 statement 讀進一支證券的全部版本與證據，選擇規則
（`visible_at`、`market_order`）抽成函數，由 `resolve` 與批次路徑共用，兩條路徑
不可能各自漂移。測試逐一比對兩者在每個截止點選出的版本與證據。

### 分段，而不是逐日重算

一筆較早交易日的更正若在較晚才發布，它會改變那個瞬間之後的每一個值，而不會
動到之前的。所以視窗在**那些瞬間**切段：段內可見歷史固定，一次計算產生段內每一列。
切段條件：觀察日 D 併入前一日的段，除非有某個交易日 ≤ 前一日的版本，在兩個截止點
之間發布。沒有遲到的更正時只有一段。

一個交易日自己的價格晚於自己的截止點才發布時，那一天在滾動序列裡**沒有列**——
在那個瞬間市場算不出它。

### 舊系統的 technical_indicators 是兩個 derivation

法人連續買賣天數的輸入領域與 release 時刻都不同，合成一個 derivation 會讓一邊的
可見性決定另一邊。連續天數是 26-b 的 `institutional_streaks:v1`。

### 「還沒算得出來」是一個答案

`ma240` 在第四十天沒有值，這與「這一天不在序列裡」不同。前者是那一列的 `None`，
後者是沒有那一列。

## 踩到的坑

### `publication_evidence` 沒有依證據目標查詢的索引

**原本以為**慢的是 resolver 要逐日呼叫。實測是單一次 resolve 要 600 ms：
2,200 萬列的 `publication_evidence` 有 append 用的索引、有依資料集與發布順序
解析的索引，就是沒有「這一個版本的證據」——而那是每一次 resolve 都在問的問題。
匯入從來沒踩到：它寫證據，不查證據。批次路徑的 `target IN (...)` 也需要同一個
索引。

### release rule 每天讀一次資料庫

改成即時計算後第一次量測是每支 0.75 秒，剖析顯示七成時間在 `ReleaseRuleService`：
每個觀察日都重新 SELECT 同一條 rule，一支證券 1,627 次。加上 `instants_for`
（一條 rule 讀一次，求值共用 `resolve` 的同一段程式）後降到 0.19 秒。

### 測試把知識截止點寫死成日期

第一版測試的 `KNOWLEDGE = 2026-09-23 00:00 UTC`，而 fixture 的證據
`recorded_at` 是測試執行的當下。過了那個時刻，每條測試都看不到任何證據——一顆
隔天就會引爆的定時炸彈。改成「現在 + 1 天」。

### 第一版實體化時的坑，現在已經不存在

一個 statement 綁不下一支證券的序列（50 萬個 bind 參數）、單一程序太慢而必須分片、
對帳把 7,600 萬列讀進記憶體會 OOM——三者都隨著不寫結果列而消失。
## 全市場即時計算

對帳腳本逐支證券呼叫 `rolling`，與讀者拿到的完全相同，並記錄每支的耗時：

```text
證券      p50       p95       max       合計
2,568    0.183 s   0.216 s   0.589 s   410 s
```

每支是 2020-01-02 → 2026-09-11 的完整序列（最長 1,627 個交易日 × 22 個指標）。
`derived_metric_versions` 與 `derived_computation_runs` 在計算前後都是 0 列。
## 對帳

即時計算版本重跑全市場與子集兩輪，結果與第一版實體化的逐項相同：證券日數、
`legacy_only` 0、價格類 0 筆超過容差、子集 ma5 `null_mismatch` 40、vma5／vma10
差異 4,508／9,008。以下數字為即時計算版本。

```text
scripts/reconcile_technical_indicators.py --start 2020-01-02 --end 2026-09-11 \
    --knowledge-as-of 2026-09-23T12:00:00+08:00
```

```text
                     全市場        交易日集合相同的 1,371 支
ours（證券日）       3,470,031     1,971,257
legacy               2,931,379     1,971,257
legacy_only                  0             0
ours_only              538,652             0
```

**`legacy_only` 是 0**：舊系統有的每一個 (證券, 日期) 我們都有。`ours_only` 是舊
scraper 從未收集的證券。

### 價格類指標逐筆相同

ma5/10/20/60/120/240 與 bb_upper／bb_middle／bb_lower 共比對 205 萬–286 萬筆，
**超過 1e-6 容差的有 0 筆**，最大差異 1.1e-13——浮點結合順序，不是語意。

### 其餘差異全部歸因，沒有一筆未解釋

對帳分兩輪跑。第一輪全市場，第二輪只比對**交易日集合完全相同**的 1,371 支證券
（`--securities-file`），把「視窗涵蓋的日子不同」這一類隔離掉。剩下的差異全部落在
三類：

**A. 舊系統丟掉沒有成交的日子。** 官方檔案那一天有列（`volume` 有值、三價為
NULL），舊 scraper 把它刪了（audit：`daily_quotes` 的最小成交量是 1）。同樣叫
「5 日視窗」，兩邊涵蓋的日子就不同。實測 2,066 支舊系統證券中 **695 支**我們多出
日期，共 26,290 個證券日；我們的歷史有 52,679 筆三價為 NULL 的官方列。
表現為 ma 的 `null_mismatch`（全市場 ma5 51,436 筆）與 vma 的值差。
我們的值是對的：那一天存在，只是沒有成交。

**B. 舊系統凍結了 2026-03-27 的不完整成交量。** 交易日集合相同的子集裡，
volume 不同的只有 **902 筆，全部在 2026-03-27**；close、high、low 差異 **0 筆**。
這正是 ROADMAP Step 28 記載的舊系統缺陷：那天的檔案 14:10 寫入，在零股交易確定
之前，`skip if exists` 讓它再也沒被重抓。vma5 在子集裡差 4,508 筆 ≈ 902 × 5，
vma10 差 9,008 ≈ 902 × 10，逐一對上視窗長度。

**C. 轉板的證券，每個來源一條序列。** 交易日集合相同的子集裡，K、D、RSI 6/12、
MACD 的**每一筆**超過容差的差異都屬於同樣 10 支證券，而那 10 支正是視窗內有兩個
價格來源的證券（全市場 14 支）：

```text
6446  tpex_otc_quotes  2020-01-02 → 2024-01-24   991 天
      twse_mi_index    2024-01-25 → 2026-09-11   636 天
```

舊系統把轉板前後接成一條序列；CLAUDE.md §30 與 Step 17-a 要求來源歷史各自獨立，
所以我們是每個來源一條，指數移動平均在轉板日重新暖機。同一組 10 支證券也解釋了
子集裡 ma5 僅有的 40 筆 `null_mismatch`——10 支 × 新來源開頭的 4 天。

**沒有第四類。** 扣掉 A、B、C 之後，指數類指標在 197 萬個證券日上與舊系統的差異
是 0 筆。原本預期會看到的「舊系統 500 日 buffer 暖機殘留」實際上不存在：
alpha = 1/3 的遞推衰減得夠快，舊系統每次增量重算的起點都在殘留消失之後。
報告裡保留了這個分類欄位，但實測值是空的。

## 驗收

| ROADMAP 驗收條件 | 結果 |
| --- | --- |
| 每個公式對固定 fixture 的值與舊系統逐欄相同 | **PASS**。fixture 取自 legacy 自己的輸出，2330 與 1418 共 717 天 × 22 欄；移植前先對兩支證券的全部 6.7 年比對過，最大差異 1.28e-09 |
| 滾動序列每一列等於該日截止點的單一 PIT context 計算 | **PASS**。`test_rolling_and_on_demand_agree_for_one_context`，以及更正與遲到發布兩種情境下逐日比對（`test_a_correction_inside_the_window_splits_the_series_where_it_lands`、`test_a_price_published_after_its_own_cutoff_has_no_row_that_day`）。把切段邏輯改成永遠一段時，後兩條失敗 |
| `market_history` 與逐 key 的 `resolve` 選出相同版本 | **PASS**。`test_the_history_resolves_every_key_as_resolve_does`：七個截止點 × 五個交易日，含晚到的第二個版本 |
| 觀察日 D 不依賴 `cutoff(D)` 之後才可見的輸入 | **PASS**。`test_a_later_correction_does_not_reach_back_into_an_earlier_date`（7-01 的更正在 7-20 發布，7-05 的 ma5 維持 14 而不是 31.8）與 `test_a_day_after_the_window_cannot_change_a_value` |
| 定義冪等註冊 | **PASS**。`test_registering_the_same_definition_twice_stores_one_row`；語意改了而版本沒改會被拒絕 |
| 與舊系統 `technical_indicators` 的價量欄位對帳，差異逐類說明 | **PASS**。見上；價格類 0 筆超過容差，其餘三類全部歸因 |
| 計算不寫入任何結果 | **PASS**。`test_computing_writes_nothing` |

測試：19 條（5 條 unit、14 條 integration）。全套 1,185 條通過。
## 量測到的、已經決定的事

第一版量到長格式實體化約 740 bytes／metric-day、`technical_indicators:v1` 單獨
56 GB。已決定：§17 改為預設即時計算，26-b–26-e 沿用同一個 service 形狀與
`market_history`，只換計算器與輸入。
## 已知限制

- **全市場的單日面板要逐支計算。** KD、RSI、MACD 是無限記憶的指數平均，必須從
  證券第一筆可見行情開始暖機，不能只讀最近 240 天。目前單支 0.19 秒，全市場一天的
  面板約 8 分鐘。Step 27（API）要依實際查詢型態決定：一次讀全市場的批次
  `market_history`，或經量測後只實體化最常用的部分（§17 的正式路徑）。
- **轉板的證券沒有連續序列。** 14 支證券在視窗內換過市場，每個來源各一條序列，
  指數類指標在轉板日重新暖機。這是 §30 的要求，不是缺陷，但下游若需要連續序列，
  需要一個明確的跨來源接續政策（要 ADR）。
- **更正的路徑目前沒有真實資料驗證過。** 這份 2020–2026 的歷史裡
  `daily_price` 的修訂數是 0：每個 (證券, 來源, 交易日) 只有一個版本、一筆
  assertion 證據。分段邏輯與「更正不往回改寫」的行為因此只有測試在驗證。
  第一筆真正的 revision 要等 Step 28 的前向抓取回頭重抓最近期間才會出現，
  屆時應該回來重跑一次這個 step 的對帳。
- **`market_history` 只支援單一版本表。** sealed aggregate（財報、TDCC）依 seal
  可見，會被明確拒絕；26-c、26-e 需要時再擴充。
- **`publication_evidence` 只補了 daily_price 目標的索引。** 其餘十六個證據目標
  維持原狀，等各自的 step 需要時再加。
