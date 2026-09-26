# ADR-0030：歷史產業分類——交易所產業類別調整公告，分類期間在查詢時推出

狀態：**Accepted**（2026-09-26 owner 決定），ROADMAP Step 39 實作（39-a／39-b／39-c）。
起因：`stock-model-selection` 的資料需求（`docs/requests/industry-classifications.md`）。

## 背景

`stocks.industry` 是今天 ISIN 清單的產業別，就地更新、沒有歷史。證交所與櫃買中心 2023-07-03
新增四個類別並調整了上百家公司，把今天的分類套在那之前的日期，用到的是當時還不存在的資訊；
2020 年以後下市的公司 `industry` 是 NULL。下游要以 PIT 的方式用產業：排除金融保險業、同產業排名、
回測的產業曝險。

來源研究見 ROADMAP Step 39 與 audit §4.15。要點：

- 證交所 `MI_INDEX?type=<類別>` 以**今天**的分類重建歷史，不能當歷史。
- 兩個交易所的**產業類別調整公告**逐家寫明原類別、新類別、發文日與實施日期。
- 櫃買中心 `afterTrading/otc?type=<類別>` 是**當日**的分類（2022-07-01 的貿易百貨有 15 檔，
  2023-07-03 為 0 檔；4419 在 2024-06-03 由紡織纖維變觀光餐旅），與公告一致。

## 決策

### 1. 觀測資料：`industry_changes`，公告中的一次變更一列

- key `(stock_id, source, effective_date)`，值是 `announced_on`（發文日期）、`document_number`
  （發文字號）、`old_industry`、`new_industry`，**照公告原文**；`fetch_id` 是公告內文，
  `attachment_fetch_id` 是提供原類別的 PDF 附件（只有 2023 年的兩則公告需要）。
- 來源 `twse_announcement`（上市）與 `tpex_announcement`（上櫃）各自獨立，不合併（CLAUDE.md §30）；
  source 決定市場，所以不另存 market。
- 只新增有變的列，與其他觀測表相同（ADR-0027）。同一 key 已由**另一則**公告持有時拒寫並報告，
  不覆蓋：否則存下的結果取決於讀公告的順序（CLAUDE.md §19）。實際資料中沒有這種情形；
  出現時由 owner 決定。
- 生效日早於 2020-01-02 的變更不存（v1 的資料窗）。
- 少了它，就沒有任何「某日的分類是什麼、何時公開」的依據。

### 2. 觀測資料：`industry_observations`（39-b）

類股行情某日把某檔列在哪一類，key `(stock_id, source, trade_date)`，存產業代碼與 fetch。
來源 `tpex_otc_quotes`（當日分類）用於已結束上櫃期間的錨點與上櫃對帳；`twse_mi_index`
（今天的分類重建）只當下市公司的最後已知產業。少了它，已結束期間的錨點與對帳沒有可追溯的出處。

### 3. 分類期間在查詢時推出（39-c），不另建期間表

與 `adjusted_prices_pit:v1` 同理：期間完全由上面兩張表、`listings` 與程式常數決定，存一份只會
與它們不一致。每段期間帶 `effective_from`、`effective_to`、`available_at`：

- 第一次已知變更之前的分類（或從未變更的），自其生效起日（2020-01-02 或上市日）即公開
  （owner 決定 3）：當時正在使用的分類本來就是公開的，只是 Data Center 從之後的公告才得知。
- 之後每段自其公告的 release rule 時點公開。
- 錨點：上市中的期間用 `stocks.industry`（今天的 ISIN）；已結束的上櫃期間用最後交易日的櫃買類股
  行情；已結束的上市期間用 `MI_INDEX?type=` 的最後已知類別（owner 決定 4、5）。

### 4. 公開時點：release rule `industry_announcement_next_day@1`

公告只有發文日期、沒有時刻：自發文日**隔天 00:00 Asia/Taipei** 起公開（owner 決定 2，ADR-0020 的
release rule）。類別改名與合併以其公告（2023-03-28）的隔天為準。2019 年以後的公告實施日都在發文後數週。

### 5. 產業代碼與類別本身的變動是程式常數

`stock_data_center.v2.industry`：ISIN 的產業別代碼表（`class_i.jsp?kind=1`，兩個市場共用、名稱與
`stocks.industry` 相同，兩個交易所的類股行情也用這套代碼）；公告用名到代碼的對應（去掉結尾的「業」
比對，另列觀光事業、電子商務）；2023-07-03 起 16 改名觀光餐旅、35–38 新增、櫃買的 34 與 18 併入 36 與 38。
寫入時每個名稱都必須對應得到代碼，且原類別在實施日前一天、新類別在實施日當天存在於該市場，否則整則公告
quarantine。

### 6. 接受的缺口

- 公告的完整性無法完全證明：從未出現在公告裡的變更抓不到。佐證是變更鏈一致性（每次的原類別接得上
  前一次、最後一次的新類別等於錨點、每段分類在其期間存在），上櫃另以類股行情逐檔對帳（39-c）。
  上市沒有當日分類的來源，缺口照實揭露。
- 公告被撤回或更正的情形尚未見過；同一公告內容改變時新增一列（revision，自 `recorded_at` 起可見），
  另一則公告取代時如 §1 拒寫。

## 結果

39-a 交付 `industry_changes`、公告 adapter、release rule 與代碼常數；39-b、39-c 依 ROADMAP。
