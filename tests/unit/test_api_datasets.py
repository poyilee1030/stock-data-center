"""Step 27-b: the API's dataset registry agrees with the tables and sources behind it."""

from __future__ import annotations

from stock_data_center.api.datasets import DATASETS, HIDDEN, UNSOURCED
from stock_data_center.v2 import visibility


def test_every_observed_family_is_served_but_financial_reports() -> None:
    # Financial reports, with their facts, are Step 27-c's.
    served = {d.table.name for d in DATASETS.values()}
    assert served == set(visibility.FAMILIES) - {"financial_reports"}


def test_every_unsourced_entry_names_a_real_source_and_column() -> None:
    for name, by_source in UNSOURCED.items():
        dataset = DATASETS[name]
        for source, columns in by_source.items():
            assert source in dataset.sources, (name, source)
            assert columns <= set(dataset.columns), (name, source, columns - set(dataset.columns))
            assert not columns & set(dataset.keys), (name, source)


def test_storage_detail_is_never_a_column() -> None:
    # Provenance is rendered from the fetch ids; availability from published_at;
    # a retracted event is not returned at all.
    storage = {"fetch_id", "detail_fetch_id", "published_at", "retracted"}
    assert storage <= HIDDEN
    for dataset in DATASETS.values():
        assert not set(dataset.columns) & storage
        assert "recorded_at" in dataset.columns


# The corporate-action observation fields an adapter can set, and the stored
# column each becomes (`stock_data_center.v2.corporate_actions._row`).
_OBSERVATION_COLUMNS = {
    "cash_dividend_per_share": "cash_dividend_per_share",
    "free_share_ratio": "free_share_ratio",
    "rights_ratio": "rights_ratio",
    "subscription_price": "subscription_price",
    "old_shares": "old_shares",
    "new_shares": "new_shares",
    "capital_reduction_cash_return_per_share": "cash_return_per_share",
    "official_rights_dividend_value": "rights_dividend_value",
}


def test_no_column_an_adapter_can_fill_is_called_unsourced() -> None:
    # Code review of #61: TWTAUU fills rights_ratio and subscription_price for a
    # reduction with a cash increase, though no such event is in stockdc_backfill.
    # What an adapter can set is read from its code, not from the data it has met.
    import inspect
    import re

    from stock_data_center.v2.corporate_actions import FEEDS

    for source, unsourced in UNSOURCED["corporate-actions"].items():
        code = "".join(inspect.getsource(type(adapter)) for adapter in FEEDS[source]
                       if adapter is not None)
        filled = {column for field, column in _OBSERVATION_COLUMNS.items()
                  if re.search(rf"\b{field}\s*=", code)}
        assert not filled & unsourced, (source, sorted(filled & unsourced))
