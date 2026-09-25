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
