# Step 26-a 驗收報告

狀態：IN REVIEW (#44)

範圍：標準衍生資料的第一部分——derivation 服務基礎，以及第一個定義
`technical_indicators:v1`。新增：

- `src/stock_data_center/derived/`：`definitions.py`（定義與冪等註冊）、
  `indicators.py`（純函數計算器）、`service.py`（即時計算與滾動 as-of 實體化）、
  `cli.py`
- migration `c7f4a1e08b23`（`dataset_catalog` 的 `technical_indicators` 列，
  以及 `publication_evidence` 依證據目標查詢的索引）
- `scripts/materialize_technical_indicators.sh`（分片、可續跑、自我終結）
- `scripts/reconcile_technical_indicators.py`
- fixture `tests/fixtures/step26a_technical_indicators_legacy.json`

## Step 26 為什麼拆成五個

七個 derivation 各自要註冊定義、實作計算、實體化滾動序列、證明實體化與即時計算
一致，再與舊系統逐欄對帳。舊系統的計算程式本身 1,791 行。拆的縫是輸入領域，
每一段自己就是完整的契約（ROADMAP §24）。

規模：`src/` +1,105 行，超過 CLAUDE.md §1 的約 800 行門檻。再往下拆會切在
`service.py` 中間——即時計算與滾動實體化共用同一組輸入解析與分段邏輯，把其中
一半先合併就是「合併了半個契約」，正是 §1 明文禁止的切法。所以這一段維持完整，
而超出的部分在這裡記下來而不是假裝沒有。四個檔案的分佈：

```text
service.py       495   分段、可見歷史、即時計算、實體化
indicators.py    244   純函數公式
cli.py           177   分片與 manifest
definitions.py   146   定義與冪等註冊
__init__.py       31
metadata.py      +12   索引宣告
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

### 分段，而不是逐日重算

滾動序列不是對整段歷史做一次計算：一筆較早交易日的更正若在較晚才發布，它會改變
那個瞬間之後的每一個值，而不會動到之前的。所以視窗在**那些瞬間**切段：段內可見
歷史固定，一次計算產生段內每一列。沒有遲到的更正時只有一段。
逐日重新解析會是 O(N²)——2330 一支就是 260 萬次 resolver 呼叫。

### 舊系統的 technical_indicators 是兩個 derivation

法人連續買賣天數的輸入領域與 release 時刻都不同，合成一個 derivation 會讓一邊的
可見性決定另一邊。連續天數是 26-b 的 `institutional_streaks:v1`。

### 「還沒算得出來」是一個答案，要存

`ma240` 在第四十天沒有值，這與「這一天不在序列裡」不同。沒有值的列以
`json_value` 的 JSON null 寫入。`sa.null()` 與 Python `None` 在 JSON 欄位上不同：
後者會被存成 JSON 的 `null`，不是 SQL NULL——第一版就是這樣違反了「恰好填一欄」
的 CHECK。

## 踩到的坑

### `publication_evidence` 沒有依證據目標查詢的索引

**原本以為**慢的是 resolver 要逐日呼叫。實測是單一次 resolve 要 600 ms：
2,200 萬列的 `publication_evidence` 有 append 用的索引、有依資料集與發布順序
解析的索引，就是沒有「這一個版本的證據」——而那是每一次 resolve 都在問的問題。
匯入從來沒踩到：它寫證據，不查證據。

加上部分索引後，一支證券的可見歷史從 16 分鐘變成 3.1 秒；沒有它，全市場要 29 天。

### 一個 statement 綁不下一支證券的序列

**原本以為**一次 insert 就好。1,622 個交易日 × 22 個指標 × 14 個參數 = 50 萬個
bind 參數，遠超過 65,535 的上限。失敗的恰好是歷史最長、最值得要的那些證券，
而五天份的 fixture 永遠測不到。回歸測試用 400 天的序列把它釘住。

### 分片是必要的，不是優化

單一程序 107k 列／分；四個分片 250k 列／分。證券之間彼此獨立，分片依排序後的
位置切，寫入互不重疊。

### 對帳的第一版會把自己撐爆

**原本以為**兩邊各讀進記憶體再比對就好。我們有 7,600 萬列、舊系統 293 萬列——
那不是比對，是 OOM。改成逐月切片，每個指標都以自己的觀察日為 key，所以一個月
自成一段。

## 全市場實體化

```text
scripts/materialize_technical_indicators.sh \
    postgresql+psycopg://.../stockdc_backfill 2026-09-22T00:00:00+08:00 4
```

```text
來源              證券    列數          每分片耗時
twse_mi_index    1,461   42,968,750    約 108 分鐘 × 4
tpex_otc_quotes  1,121   33,021,626    約 84 分鐘 × 4
合計             2,582   75,990,376    約 3.5 小時（牆鐘）
```

八個分片的 `failures` 全部是空的。`derived_metric_versions` 約 56 GB。
`(definition, security, observation_date, metric_code)` 重複 0 筆——分片依排序後的
位置切，寫入互不重疊，而語意 identity 索引是最後一道保險。

## 對帳

```text
scripts/reconcile_technical_indicators.py --start 2020-01-02 --end 2026-09-11
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
這正是 ROADMAP Step 27 記載的舊系統缺陷：那天的檔案 14:10 寫入，在零股交易確定
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
| 實體化結果與同一個 PIT context 的即時計算完全相同 | **PASS**。`test_materialized_and_virtual_agree_for_one_context` |
| 觀察日 D 不依賴 `cutoff(D)` 之後才可見的輸入 | **PASS**。`test_a_later_correction_does_not_reach_back_into_an_earlier_date`（7-01 的更正在 7-20 發布，7-05 的 ma5 維持 14 而不是 31.8）與 `test_a_day_after_the_window_cannot_change_a_materialised_value` |
| 定義冪等註冊 | **PASS**。`test_registering_the_same_definition_twice_stores_one_row`；語意改了而版本沒改會被拒絕 |
| 與舊系統 `technical_indicators` 的價量欄位對帳，差異逐類說明 | **PASS**。見上；價格類 0 筆超過容差，其餘三類全部歸因 |

測試：14 條（5 條 unit、9 條 integration），全部先紅後綠。

## 量測到的、要決定的事

`derived_metric_versions` 是長格式，一個 metric-day 一列，實測約 **740 bytes／列**
（7,600 萬列、56 GB，含九欄的語意 identity 索引）。26-b–26-e 還有六個 derivation，
全部做完會落在數百 GB。這個 step 照 Step 10 既有的儲存契約做完，沒有自行改
schema——但這個數字應該在 26-b 開始前決定要不要處理，而不是等到 Step 30。

## 已知限制

- **轉板的證券沒有連續序列。** 14 支證券在視窗內換過市場，每個來源各一條序列，
  指數類指標在轉板日重新暖機。這是 §30 的要求，不是缺陷，但下游若需要連續序列，
  需要一個明確的跨來源接續政策（要 ADR）。
- **`derived_metric_versions` 沒有 source 欄位。** 序列是逐來源計算的，來源記在
  `input_dataset_identity` 裡，不在語意 identity 索引裡。實測沒有任何
  `(security, date, metric)` 重複，因為一支證券同一天只在一個市場交易；但 API
  （Step 28）回傳時要讓讀者看得到是哪個來源。
- **更正的路徑目前沒有真實資料驗證過。** 這份 2020–2026 的歷史裡
  `daily_price` 的修訂數是 0：每個 (證券, 來源, 交易日) 只有一個版本、一筆
  assertion 證據，因為整段歷史是一次性補抓的，我們從來沒有「先看到原始值、
  後看到更正值」。分段邏輯與「更正不往回改寫」的行為因此只有測試在驗證。
  第一筆真正的 revision 要等 Step 27 的前向抓取回頭重抓最近期間才會出現，
  屆時應該回來重跑一次這個 step 的對帳。
- **`publication_evidence` 只補了 daily_price 目標的索引。** 其餘十六個證據目標
  維持原狀，等各自的 step 需要時再加。
