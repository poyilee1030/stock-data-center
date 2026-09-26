"""The derived datasets the API serves (Step 27-c), under their own public names.

A stored dataset follows the latest inputs (CLAUDE.md §43) and is filtered by
`information_as_of` alone: a row is served once every input it was computed
from is public (`visibility.derived_rows`). It has no knowledge axis, so a
historical `knowledge_as_of` or `system_as_of` is refused (owner, 2026-09-25).
`technical-indicators-pit` is `technical_indicators_pit:v1`, computed on demand
under full PIT (§43, §46), and so is `adjusted-prices-pit`,
`adjusted_prices_pit:v1` (Step 36), which keeps PIT corporate-action visibility.
"""

from __future__ import annotations

from dataclasses import dataclass

from stock_data_center.api.datasets import DATASETS
from stock_data_center.v2 import derived_store
from stock_data_center.v2.adjusted_prices import ADJUSTED_PRICES_PIT_V1
from stock_data_center.v2.derived import TECHNICAL_INDICATORS_PIT_V1, Definition
from stock_data_center.v2.derived_store import StoredDataset

# An input table's public name (§55).
PUBLIC = {d.table.name: d.name for d in DATASETS.values()}


def describe(definition: Definition) -> dict:
    """What §42 asks a definition to identify, with inputs by public name."""
    return {
        "dataset_code": definition.dataset_code,
        "derivation_version": definition.derivation_version,
        "formula": definition.formula_specification,
        "inputs": [PUBLIC[table] for table in definition.input_tables],
        "calendar_timezone": definition.calendar_timezone,
        "calendar_convention": definition.calendar_convention,
        "price_adjustment_convention": definition.price_adjustment_convention,
    }


@dataclass(frozen=True)
class Derived:
    name: str
    stored: StoredDataset
    description: str

    @property
    def period(self) -> str:
        [day] = (c.name for c in self.stored.table.primary_key.columns
                 if c.name not in ("stock_id", "source"))
        return day

    @property
    def columns(self) -> list[str]:
        return [c.name for c in self.stored.table.columns]


DERIVED: dict[str, Derived] = {
    d.name: d
    for d in (
        Derived("technical-indicators", derived_store.TECHNICAL_INDICATORS,
                "MA, VMA, KD, RSI, MACD and Bollinger bands on raw official close."),
        Derived("institutional-streaks", derived_store.INSTITUTIONAL_STREAKS,
                "Consecutive days each institutional party was a net buyer or seller."),
        Derived("institutional-cumulative-flows", derived_store.INSTITUTIONAL_CUMULATIVE_FLOW,
                "Running sums of trust and dealer net shares: a proxy, not a holding."),
        Derived("shareholding-concentrations", derived_store.SHAREHOLDING_CONCENTRATION,
                "Small, mid and large holder ratios from the TDCC levels."),
        Derived("margin-metrics", derived_store.MARGIN_METRICS,
                "Margin and short usage, balance changes and short-cover pressure."),
        Derived("short-interest-metrics", derived_store.SHORT_INTEREST_METRICS,
                "Securities-lending balance change and sold-over-returned ratio."),
        Derived("valuation-metrics", derived_store.VALUATION_METRICS,
                "Computed TTM EPS, PE, PE percentile and ROE (not official-valuations)."),
    )
}

PIT_REFERENCE = "technical-indicators-pit"
PIT_REFERENCE_DEFINITION = TECHNICAL_INDICATORS_PIT_V1

ADJUSTED = "adjusted-prices-pit"
ADJUSTED_DEFINITION = ADJUSTED_PRICES_PIT_V1
