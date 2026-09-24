# Institutional Flow and Securities Financing

Five observed exchange-published daily datasets. The measured source facts are
in `docs/source_field_audit.md` §4.3–§4.5; the storage design is ADR-0027.

| Table | Key | Content |
| --- | --- | --- |
| `institutional_flows` | `(stock_id, source, trade_date)` | published foreign / foreign-dealer / trust / dealer-self / dealer-hedge buy, sell and net; dealer and total net |
| `institutional_market_flows` | `(source, trade_date, institution)` | published market-wide buy, sell and net per institution |
| `foreign_holdings` | `(stock_id, source, trade_date)` | issued, investable and held shares; investable, held and foreign legal-limit ratios |
| `margin_trading` | `(stock_id, source, trade_date)` | margin and short buys, sells, repayments, balances and next-day limits; offset balance |
| `securities_lending` | `(stock_id, source, trade_date)` | previous balance, sold, returned, signed adjustment, balance, next limit, next available limit |

Every table is append-only: a row is added only when a published value changes,
and each row names its fetch. `source` keeps TWSE and TPEx histories apart;
nothing merges, averages, or picks the latest one. OTC foreign holding comes
from MOPS `t13sa150_otc` only (audit §4.4). A trade date is public at release
rule `exchange_daily_settled@1`, 03:00 Asia/Taipei on the next day; a later,
different value from its own `recorded_at`.

Not stored, by owner decision (ADR-0027): the TPEx-only margin utilization
ratios, the mainland legal-limit ratio, change reason and filing date of
foreign holding, and the SBL note.

## Stock quantities are shares

Every per-stock quantity is stored in **shares (`股`)**; lots (`張`) are never a
storage unit. An adapter represents each quantity as `SourceShareQuantity` with
an explicit `QuantityScale.SHARE` or `QuantityScale.LOT` and converts it with
`to_canonical()`; an untyped `Decimal` is rejected, because the unit must never
be inferred from magnitude (CLAUDE.md §72). One lot is 1,000 shares, except the
securities `margin_trading.TWSE_LOT_SHARES` lists with their evidence (008201:
100). This applies to institutional flows, foreign-holding share counts, all
margin and short quantities, and all SBL quantities including the adjustment.
The market-level institutional totals are published amounts, not per-stock
share quantities.

Gross buys and sells, holdings, balances and limits are non-negative. Net flows
stay signed and are never recomputed from buy and sell. The SBL adjustment is
signed as published. Ratios are the source's percentages, 0 to 100, with no
fraction-to-percent conversion.

## Derived-data boundary

Daily trust and dealer net flows do not give absolute holdings without a real
initial holding baseline, and no such source is known. The legacy
`trust_held_*` and `dealer_held_*` values are therefore a cumulative-flow proxy,
defined as `institutional_cumulative_flow:v1` (Step 26):

```text
trust_cumulative_net_shares
trust_cumulative_net_ratio
dealer_cumulative_net_shares
dealer_cumulative_net_ratio
```

Each is the running sum of the daily net shares from the series' first day of
the institutional file, and the ratio is that sum as a percentage of the same
day's issued shares (`twse_mi_qfiis` for TWSE, `mops_t13sa150_otc` for TPEx),
rounded to four places as legacy did; NULL without a foreign-holding row that
day. The table is `institutional_cumulative_flow` (Step 26-c). Neither value
claims ownership. An actual holding dataset would need an observed
baseline and a new derivation version, never a silent change to this proxy.

Margin usage recomputation, short-interest ratios and SBL pressure are canonical
derived (`margin_metrics:v1`, `short_interest_metrics:v1`); the composite
pressure scores are downstream. Shareholding concentration derives from
`shareholding_distributions`, not from these tables.
