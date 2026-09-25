"""The observed datasets the API serves, under names that are not table names (CLAUDE.md §55).

`UNSOURCED` names, per dataset and source, the columns that source never
publishes, so the API omits them for its rows instead of presenting a NULL as
if it were a value; a NULL anywhere else is a value the source did not give
for that row (a day without a trade has no price). Each entry cites what proves
it:

- indices: the whole-list files publish close and changes only; TAIEX's open,
  high and low come from `MI_5MINS_HIST`, which publishes no change (§52,
  audit §4.2);
- corporate actions: what each feed's adapter can set
  (`ingestion.adapters.corporate_action`, audit §4.10): the ex-right files no
  share exchange and no returned cash; TWTAUU no rights-plus-dividend value and
  no free shares, though its detail page gives a cash dividend merged into the
  reduction and the terms of a cash increase that goes with one; `revivt` none
  of those, since its adapter refuses a reduction with a cash increase rather
  than guess the ratio's unit; the par-value-change files only their prices,
  with TPEx's exchange rate. A unit test reads each adapter's code to keep this
  list from naming a column it can fill: data that has not met a case yet is no
  proof the source never publishes it (code review of #61).
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa

from stock_data_center.db import schema_v2 as v2
from stock_data_center.v2 import visibility
from stock_data_center.v2.backfill import JOBS
from stock_data_center.v2.corporate_actions import FEEDS

# Storage detail: provenance is rendered from these, and `published_at` is
# what `available_at` is computed from.
HIDDEN = frozenset({"fetch_id", "detail_fetch_id", "published_at", "retracted"})

_EX_RIGHT = frozenset({"old_shares", "new_shares", "cash_return_per_share"})
_REDUCTION = frozenset({"rights_dividend_value", "free_share_ratio"})
_PAR_VALUE = frozenset({"rights_dividend_value", "cash_dividend_per_share", "free_share_ratio",
                        "rights_ratio", "subscription_price", "cash_return_per_share"})
_WHOLE_LIST_INDEX = frozenset({"open_value", "high_value", "low_value"})

UNSOURCED: dict[str, dict[str, frozenset[str]]] = {
    "indices": {
        "twse_mi_index": _WHOLE_LIST_INDEX,
        "tpex_index_summary": _WHOLE_LIST_INDEX,
        "twse_mi_5mins_hist": frozenset({"change_points", "change_percent"}),
    },
    "corporate-actions": {
        "twse_twt49u": _EX_RIGHT,
        "tpex_exdailyq": _EX_RIGHT,
        "twse_twtauu": _REDUCTION,
        "tpex_revivt": _REDUCTION | {"cash_dividend_per_share", "rights_ratio",
                                     "subscription_price"},
        "twse_twtb8u": _PAR_VALUE | {"old_shares", "new_shares"},
        "tpex_pvchgrslt": _PAR_VALUE,
    },
}


@dataclass(frozen=True)
class Dataset:
    name: str
    table: sa.Table
    description: str

    @property
    def family(self) -> visibility.Family:
        return visibility.FAMILIES[self.table.name]

    @property
    def period(self) -> str:
        return self.family.period(self.table).name

    @property
    def keys(self) -> tuple[str, ...]:
        return self.family.keys

    @property
    def sources(self) -> list[str]:
        if self.table is v2.corporate_actions:
            return sorted(FEEDS)
        return sorted({job.source for job in JOBS.values() if job.table is self.table})

    @property
    def has_stock(self) -> bool:
        return "stock_id" in self.table.c

    @property
    def columns(self) -> list[str]:
        return [c.name for c in self.table.columns if c.name not in HIDDEN]

    def unsourced(self, source: str) -> frozenset[str]:
        return UNSOURCED.get(self.name, {}).get(source, frozenset())


DATASETS: dict[str, Dataset] = {
    d.name: d
    for d in (
        Dataset("daily-prices", v2.daily_prices, "Daily quotes of each stock, whole market."),
        Dataset("indices", v2.index_prices, "Published market indices by published name."),
        Dataset("official-valuations", v2.valuations,
                "Source-published PE, PB and dividend yield (not computed valuation)."),
        Dataset("institutional-flows", v2.institutional_flows,
                "Daily net buying of foreign investors, investment trusts and dealers."),
        Dataset("institutional-market-flows", v2.institutional_market_flows,
                "The same, summed over the market."),
        Dataset("foreign-holdings", v2.foreign_holdings,
                "Foreign ownership, limits and issued shares."),
        Dataset("margin-trading", v2.margin_trading, "Margin purchase and short sale balances."),
        Dataset("securities-lending", v2.securities_lending, "Securities borrowing and lending."),
        Dataset("shareholding-distributions", v2.shareholding_distributions,
                "TDCC weekly holding levels."),
        Dataset("monthly-revenues", v2.monthly_revenues,
                "Monthly revenue with its published comparatives."),
        Dataset("corporate-actions", v2.corporate_actions,
                "Events the exchanges executed, from their result files."),
    )
}
