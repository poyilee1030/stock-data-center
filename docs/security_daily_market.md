# Stock Universe and Daily Market Data

The measured source facts are in `docs/source_field_audit.md` §4.1, §4.11 and
§4.14; the storage design is ADR-0026 (universe), ADR-0027 (tables) and
ADR-0028 (listing spans).

## The universe

The universe is listed (`上市`, `sii`) and OTC (`上櫃`, `otc`) common stocks
only: no ETFs, ETNs, preferred shares, TDRs, beneficiary certificates, warrants
or the innovation board (`創新板`, whose CFI code is the same `ESVUFR` as ordinary
common stock, which is why a source's category and not the CFI code decides).

`stocks` holds each company's identity: the official code `stock_id`, its
name and industry, and the fetch they came from. It has every stock in the
`股票` section of today's TWSE ISIN lists (`strMode=2` and `4`,
`stock_data_center.v2.universe`) and, since Step 38-a, every company delisted
since 2020-01-02 that an official source proves to be a common stock.

`listings` holds when and where each traded: one span `[listed_on,
delisted_on)` per market, from the exchanges' listing and delisting tables
(`stock_data_center.v2.listings`, ADR-0028). A stock that moved between markets
has one span on each; `delisted_on` is NULL while it is listed. `listed_on` is
NULL for a listing older than the exchange's table.

`stock_id` is the identity everywhere: listed and OTC common stocks keep one
code for life. `stocks` is today's state, not history: no name or industry
history is kept. Both tables are reference data refreshed in place, not
point-in-time.

The adapters fetch only the stocks with an open span, so the datasets still
hold no row for a company delisted before its fetch. That survivorship bias is
accepted and must be disclosed to consumers (ADR-0026); Step 38-b is to fetch
the delisted companies' history.

## Daily prices

`daily_prices` holds one append-only row per `(stock_id, source, trade_date)`
from the whole-market feeds `twse_mi_index` (`MI_INDEX`, `type=ALLBUT0999`) and
`tpex_otc_quotes` (`afterTrading/otc`, the audit's `stk_wn1430`), one file per
market and trade date. Only stocks with an open listing span are written. A trade date is
public at release rule `exchange_daily_settled@1`, 03:00 Asia/Taipei on the next
day; a later, different value from its own `recorded_at`.

| Legacy `daily_quotes` concept | Column |
| --- | --- |
| OHLC | `open_price`, `high_price`, `low_price`, `close_price` |
| volume | `volume` (shares) |
| trade value | `trade_value` (TWD) |
| trade count / transactions | `trade_count` |
| price change | `price_change` |
| price direction | `price_direction` |
| last bid/ask price and volume | `last_bid_price`, `last_ask_price`, `last_bid_volume`, `last_ask_volume` |

`date`, `symbol`, `market` and `name` are not dropped: they are the key's
`trade_date`, the key's `stock_id`, and the stock's `stocks` row and listing spans. `price_direction`
is TWSE-only except for the TPEx 不比價 marker (除息 / 除權 / 除權息), stored as
`X`. The bid/ask volume is shares for TWSE and lots converted to shares for
TPEx, published from 2020-04-30 (audit §4.1). No order-book depth is stored:
the files publish one level. `pced_file`, `pced_row` and `pced_col` are parser
coordinates, not values; the raw file keeps the source representation.

The Step 9 per-security pilot sources `twse` and `tpex` are not kept
(ADR-0027): their field set differed, and only the whole-market feeds are
ingested.
