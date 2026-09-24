# Market Indices, Corporate Actions, and Official Valuation

Three observed datasets. The measured source facts are in
`docs/source_field_audit.md` §4.2, §4.6 and §4.10; the storage design is
ADR-0027.

| Table | Key | First row public at |
| --- | --- | --- |
| `index_prices` | `(source, index_name, trade_date)` | `exchange_daily_settled@1`: 03:00 on the next day |
| `valuations` | `(stock_id, source, trade_date)` | `exchange_daily_settled@1` |
| `corporate_actions` | `(stock_id, source, ex_date)` | `corporate_action_ex_date@1`: 00:00 on the ex-date |

Every table is append-only; a corrected value is a later row, public from its
own `recorded_at`. `source` keeps histories independent.

## Indices

An index is identified by its source and its published `category:name` string,
which is how each source names it. v1 keeps 126: 加權 and 櫃買, each as a price
and a total-return index, and every sector index both exchanges compile
(`stock_data_center.v2.indices`, owner decision 2026-09-23). The whole-list
sources publish close, change points and change percent. Open, high and low
exist for the TAIEX alone, from `MI_5MINS_HIST`; its close must equal
`MI_INDEX` on every date or the month is quarantined (CLAUDE.md §52). No source
publishes index trade value.

Levels and changes are points; `change_percent` is percentage points.

## Official valuation

`valuations` holds only what the source published: PE, PB, dividend yield, and
the source's dividend year and report period. It never stores a Data
Center-computed PE, percentile, TTM EPS or ROE as if it were observed; those are
canonical derived `valuation_metrics:v1` (Step 26), distinguishable from the
published values. Official PE and PB are multiples; dividend yield is
percentage points.

## Corporate actions

Corporate actions come from the six exchange result feeds: TWSE `TWT49U`,
`TWTAUU`, `TWTB8U` and TPEx `exDailyQ`, `revivt`, `pvChgRslt`. Each records an
event the exchange executed and priced on a trading date, so the key
`(stock_id, source, ex_date)` is the §51.5 event identity: the feed plus the
executed date, which is TWSE's own detail locator. Announcement feeds never
enter this table (CLAUDE.md §51.5).

- `event_type` is the feed's own type text (息, 權, 權息, 除息, 除權, 除權息,
  退還股款, 彌補虧損, 現金減資, 變更股票面額). The legal categories stay
  distinct: they are never collapsed because some adjustment formula would treat
  them alike.
- `close_before`, `reference_price` and `rights_dividend_value` (權值+息值,
  close before minus reference price, so it may be negative) are published by
  every feed that prices the event.
- Terms: `cash_dividend_per_share`, `free_share_ratio`, `rights_ratio` (per
  1,000 divided by 1,000, so shares per share), `subscription_price`,
  `old_shares` / `new_shares` for capital reductions and TPEx par-value changes,
  and `cash_return_per_share`. Ratios keep twelve places and per-share amounts
  eight, as published.
- TWSE `TWT49U` and `TWTAUU` publish the terms only on each event's detail
  page: `fetch_id` names the list and `detail_fetch_id` the detail.
- A current-year file also lists coming events; they are counted and never
  stored until executed (`executed_through`, ADR-0019). A stored event the feed
  no longer lists is retracted by a later row with `retracted = true`.

Not stored: the announcement, record and payment dates and the earnings /
capital-surplus split, which no result feed publishes (the split is Step 33's
declaration domain); the limit prices and other terms v1 kept in
`source_terms`, which stay in the raw file.

A corporate action is never inferred from a price jump, and official daily
prices are never rewritten by one. Adjustment factors and adjusted prices are
Step 36, computed from `reference_price / close_before` on and after the
ex-date only.
