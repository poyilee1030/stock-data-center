# ADR-0019：交易所結果檔的事件身分

狀態：**Accepted**，隨 ROADMAP Step 19-a 合併生效。
落實 ROADMAP Invariant G(2) 與 §27.7；延伸 ADR-0018（公司行動契約）。

## 背景

公司行動的身分是 `(security_id, source, source_event_key)`（ADR-0018），而且修正條款
之後身分必須不變。公告類 feed（`t187ap45_L`、`mopsfin_t187ap39_O`、`TWT48U`）做不到：
被放棄的 pilot 用 `(security, dividend_year, period)` 在 TPEx 實測出 65 組重複，
代表案例 1591/108/1 對應兩個相隔一年的董事會決議日（ROADMAP §21.3）。

交易所的**結果檔**不一樣：每一列是交易所已經執行、已經算出參考價的事件，
而 TWSE 自己用 `詳細資料` 這個 locator（`1101,20240701`）指向它。

2026-09-16 對六個結果檔、2020-2026 每一年的實測（audit §4.10）另外推翻了三個
原本的假設，本 ADR 一併裁決。

## 決策

### 1. `source_event_key = "<feed>:<locator 日期>"`，其他一概不進

- TWSE 用交易所自己的 locator：`TWT49U:20240104`、`TWTAUU:20240320`、
  `TWTB8U:20250814,20250825`（停止與恢復兩個日期，照 locator 原樣保留）。
- TPEx 沒有 locator，以已執行的日期代替：`exDailyQ:20240103`、`revivt:20240205`、
  `pvChgRslt:20240909`。
- 金額、比例、類型、公司名稱、列序、adapter 版本都不得進 key（CLAUDE.md §51.5）。
  同一 locator 下條款改變，是同一事件的新 revision；列從 feed 消失，是 retraction。

`ExchangeLocator` 只接受已登記的結果檔；公告類 feed 在建構時就以
`announcement_feed` 拒絕，未知 feed 以 `unknown_feed` 拒絕。1591/108/1 保留為永久 fixture。

**原本以為** TWTAUU 的 locator 日期就是恢復買賣日。實測是 TWSE 的檔案日，
14 份明細裡全部是停止買賣日的前一天。身分照交易所給的 locator，不自行換成恢復日。

完整歷史掃描：六個 feed 各自 `(代號, locator)` 與 `(代號, 事件日)` 皆零重複。

### 2. 只有「已執行」的列才是事件

**原本以為**結果檔只列已發生的事件。實測 2026-09-16 抓的當年度檔已列出
09-17（TWT49U 35 列）、09-21（revivt 3 列）、10-19（TWTAUU，價格欄全是 `-`）。
交易所會提前公布計算結果；那些列還是計畫，不是 Invariant G(2) 所依據的已執行事實。

請求因此帶 `executed_through`：日期晚於它的列只計數（`not_yet_executed`），不產生事件，
該檔只宣稱涵蓋到 `min(end, executed_through)`。這個值在**發出工作時**決定，
不從抓取時鐘推得，理由與 ingest purpose 相同（ROADMAP §3.1）：可得性不能取決於
誰在什麼時候碰巧去看。

之後由 import 步驟負責的撤回判斷，也只能在這個涵蓋範圍內進行——
範圍外「沒出現」不代表被撤回。

### 3. `權值+息值` 是有號差值

**原本以為**它是非負金額，schema 也這樣限制。來源定義它為
除權息前收盤價 − 除權息參考價；認購價高於收盤價的現金增資會讓它為負
（TWSE 4 列、TPEx 2 列）。錯的是約束，不是資料：

- `official_rights_dividend_value` 改用 `SignedTwdAmount`，其他金額仍是非負的 `TwdAmount`；
- migration `8e4b2c7d9a13` 只把這一欄移出非負 CHECK。

### 4. 比例與每股金額的精度照來源

- 配股與認購以「每千股」公布、最多八位小數，除以 1,000 後需要十一位：
  四個比例欄改為 `NUMERIC(28, 12)`。
- TPEx 現金股利有八位小數：`TwdAmount` 放寬到八位，但每個 observation 另外檢查
  自己欄位的小數位數，因為 PostgreSQL 會默默四捨五入到欄位 scale。
- 來源公布的 `0` 代表該項不適用（TWT49U 註記），存為 NULL，不存 0。

scale 改變會改變數值的文字表示，而 business hash 由它計算；migration 雙向重算既有
revision 的 hash，否則原樣重匯會產生假 revision。downgrade 在任何變動之前，
遇到超過八位的比例或負的差值就以 `P0001` 拒絕。

### 5. 面額變更依來源能證明的程度分類

- TPEx `pvChgRslt` 公布換股率與前後面額：存為 `stock_split` / `reverse_split`，
  `old_shares = 1`、`new_shares = 換股率`，並核對換股率等於前後面額之比，不符即隔離。
- TWSE `TWTB8UDetail` 只重複列表的價格欄，**沒有換股率**：存為 `other`，保留參考價，
  `old_shares`/`new_shares` 為 NULL。不從收盤價與參考價反推比例（CLAUDE.md §51.2）。

### 6. 類型與條款不一致就隔離

息必有現金且無配股、權必有配股或認購且無現金、權息兩者皆有；有認購比例必有認購價。
TPEx 7,359 列與 TWSE 53 份明細全部成立。不一致代表來源或解析有一方錯了，
不猜哪一方。

## 後果

- Step 19 拆成四份：19-a 契約、儲存與 TPEx adapter；19-b TWSE adapter 與明細頁；
  19-c import 路徑（含撤回）；19-d 回補與 legacy 對帳。
- ETF 分割／反分割有自己的結果檔（TWSE `TWTCAU`、TPEx `etfSplitRslt`、`etfRvsRslt`），
  不在六個 feed 之內；沒有它們，0050 等 ETF 的還原價在 Step 25 會錯。
  ROADMAP 將它列為 Step 25 之前的後續工作。
- 公司名稱與 TWT49U 的 `最近一次申報*` 不進業務內容：前者會因更名、後者會因每季申報，
  讓所有過去事件產生假 revision。
