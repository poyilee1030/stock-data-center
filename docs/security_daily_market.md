# Stock Universe and Daily Market Data

The measured source facts are in `docs/source_field_audit.md` §4.1 and §4.14;
the storage design is ADR-0026 (universe) and ADR-0027 (tables).

## The universe

`stocks` is today's TWSE ISIN list of listed (`上市`, `strMode=2`) and OTC
(`上櫃`, `strMode=4`) securities under the `股票` category: common stocks only,
no ETFs, ETNs, preferred shares, TDRs, beneficiary certificates, warrants or the
innovation board (`創新板`, whose CFI code is the same `ESVUFR` as ordinary common
stock, which is why the category and not the CFI code decides). Each row holds
the official code `stock_id`, today's name, market (`sii` or `otc`), industry
and listing date, and the fetch of the list page it came from
(`stock_data_center.v2.universe`).

`stock_id` is the identity everywhere: listed and OTC common stocks keep one
code for life. `stocks` is today's state, not history: no name, industry or
market history is kept, and a stock that moved between markets carries today's
market. A stock delisted before today is not on the list and so not in the
universe anywhere; that survivorship bias is accepted and must be disclosed to
consumers (ADR-0026).

## Daily prices

`daily_prices` holds one append-only row per `(stock_id, source, trade_date)`
from the whole-market feeds `twse_mi_index` (`MI_INDEX`, `type=ALLBUT0999`) and
`tpex_otc_quotes` (`afterTrading/otc`, the audit's `stk_wn1430`), one file per
market and trade date. Only stocks in `stocks` are written. A trade date is
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
`trade_date`, the key's `stock_id`, and today's `stocks` row. `price_direction`
is TWSE-only except for the TPEx 不比價 marker (除息 / 除權 / 除權息), stored as
`X`. The bid/ask volume is shares for TWSE and lots converted to shares for
TPEx, published from 2020-04-30 (audit §4.1). No order-book depth is stored:
the files publish one level. `pced_file`, `pced_row` and `pced_col` are parser
coordinates, not values; the raw file keeps the source representation.

The Step 9 per-security pilot sources `twse` and `tpex` are not kept
(ADR-0027): their field set differed, and only the whole-market feeds are
ingested.
