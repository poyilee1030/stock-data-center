from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from stock_data_center.pit.contracts import get_contract
from stock_data_center.tdcc import (
    TDCC_OPENDATA_V1,
    TDCCBucketObservation,
    TDCCPublication,
    TDCCSnapshotObservation,
)


ROOT = Path(__file__).resolve().parents[2]


def bucket(code: str = "1", **overrides: object) -> TDCCBucketObservation:
    values: dict[str, object] = {
        "bucket_code": code,
        "holder_count": 10,
        "shares": Decimal("5000"),
        "ownership_percent": Decimal("12.5"),
    }
    values.update(overrides)
    return TDCCBucketObservation(**values)  # type: ignore[arg-type]


def test_bucket_values_are_never_silently_rounded_by_storage() -> None:
    with pytest.raises(ValueError, match="whole number"):
        bucket(shares=Decimal("1.5"))
    with pytest.raises(ValueError, match="8 decimal places"):
        bucket(ownership_percent=Decimal("1.123456789"))
    assert bucket(ownership_percent=Decimal("1.12345678")).ownership_percent == (
        Decimal("1.12345678")
    )
    assert bucket(shares=Decimal("1.000")).shares == Decimal("1")


def test_bucket_values_reject_invalid_ranges_and_identity() -> None:
    with pytest.raises(ValueError, match="holder_count"):
        bucket(holder_count=-1)
    with pytest.raises(ValueError, match="ownership_percent"):
        bucket(ownership_percent=Decimal("100.00000001"))
    with pytest.raises(ValueError, match="ownership_percent"):
        bucket(ownership_percent=Decimal("-100.00000001"))
    with pytest.raises(ValueError, match="bucket_code"):
        bucket(code="")
    with pytest.raises(ValueError, match="bucket_code"):
        bucket(code=" 1")


def test_signed_adjustment_values_are_representable_before_profile_rules() -> None:
    # Role-specific sign rules need the distribution profile; the value type
    # itself must be able to carry a signed level-16 adjustment verbatim.
    adjustment = bucket("16", holder_count=None, shares=Decimal("-2000"),
                        ownership_percent=Decimal("-0.01"))
    assert adjustment.shares == Decimal("-2000")
    assert adjustment.holder_count is None


def test_snapshot_requires_a_nonempty_distribution_with_unique_buckets() -> None:
    with pytest.raises(ValueError, match="at least one"):
        TDCCSnapshotObservation(
            snapshot_date=date(2024, 1, 5),
            distribution_schema=TDCC_OPENDATA_V1,
            distribution=(),
        )
    with pytest.raises(ValueError, match="duplicate"):
        TDCCSnapshotObservation(
            snapshot_date=date(2024, 1, 5),
            distribution_schema=TDCC_OPENDATA_V1,
            distribution=(bucket("1"), bucket("1")),
        )
    observation = TDCCSnapshotObservation(
        snapshot_date=date(2024, 1, 5),
        distribution_schema=TDCC_OPENDATA_V1,
        distribution=[bucket("2"), bucket("1")],
    )
    assert isinstance(observation.distribution, tuple)


def test_publication_time_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        TDCCPublication(
            evidence_kind="assertion",
            published_at=datetime(2024, 1, 6, 12),
            evidence_source="tdcc",
            evidence_type="official",
            quality_rank=100,
        )


def test_tdcc_resolver_contract_is_an_aggregate_keyed_by_snapshot_date() -> None:
    contract = get_contract("tdcc_snapshot")
    assert contract.is_aggregate
    assert contract.logical_key_columns == ("security_id", "snapshot_date")


def test_phase6_contract_documents_effective_time_and_defers_derivations() -> None:
    contract = (ROOT / "docs" / "tdcc.md").read_text()
    assert "snapshot_date is not a publication time" in contract
    assert "published_at = NULL" in contract
    assert "later canonical-derived phase" in contract
    assert "shareholding_concentration:v1" in contract
    assert 'COLLATE "C"' in contract
    assert "level 16 is a signed adjustment" in contract
    assert "holder_count is NULL" in contract
    assert "incomplete snapshot cannot be sealed" in contract
    assert "A profile is frozen once used" in contract


def test_tdcc_package_has_no_cache_or_http_dependency() -> None:
    package = ROOT / "src" / "stock_data_center" / "tdcc"
    source = "\n".join(path.read_text() for path in package.glob("*.py"))
    assert "redis" not in source.lower()
    assert "fastapi" not in source.lower()
