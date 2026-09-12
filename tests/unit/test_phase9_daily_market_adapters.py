from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from stock_data_center.ingestion import (
    DailyMarketRequest,
    LocalRawArtifactStore,
    RawArtifactIntegrityError,
    SourceDataError,
)
from stock_data_center.ingestion.adapters import (
    TPExDailyMarketAdapter,
    TWSEDailyMarketAdapter,
)

TWSE_PAYLOAD = {
    "stat": "OK",
    "date": "20250901",
    "title": "114年09月 2330 台積電           各日成交資訊",
    "fields": list(TWSEDailyMarketAdapter.fields),
    "data": [
        [
            "114/09/01",
            "23,022,319",
            "26,647,241,918",
            "1,150.00",
            "1,165.00",
            "1,145.00",
            "1,165.00",
            "+5.00",
            "41,416",
            "",
        ]
    ],
}

TPEX_PAYLOAD = {
    "date": "20250901",
    "code": "6488",
    "name": "環球晶",
    "stat": "ok",
    "tables": [
        {
            "fields": list(TPExDailyMarketAdapter.fields),
            "data": [
                [
                    "114/09/01",
                    "1,666",
                    "611,795",
                    "371.50",
                    "374.00",
                    "364.00",
                    "364.50",
                    "7.50",
                    "3,198",
                ]
            ],
        }
    ],
}


def encoded(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode()


def test_twse_share_and_twd_values_are_already_canonical() -> None:
    request = DailyMarketRequest("2330", date(2025, 9, 1))
    result = TWSEDailyMarketAdapter().parse(encoded(TWSE_PAYLOAD), request)

    assert result.security_name == "台積電"
    assert result.coverage_start == date(2025, 9, 1)
    assert result.rows[0].volume == Decimal(23022319)
    assert result.rows[0].trade_value == Decimal(26647241918)
    assert result.rows[0].price_direction == "+"


def test_tpex_lots_and_thousand_twd_normalize_before_observation() -> None:
    request = DailyMarketRequest("6488", date(2025, 9, 1))
    result = TPExDailyMarketAdapter().parse(encoded(TPEX_PAYLOAD), request)

    assert result.rows[0].volume == Decimal(1666000)
    assert result.rows[0].trade_value == Decimal(611795000)
    assert result.rows[0].trade_count == 3198
    assert result.rows[0].price_direction == "+"


def test_twse_x_change_marker_is_preserved_instead_of_guessed() -> None:
    payload = dict(TWSE_PAYLOAD)
    payload["data"] = [list(TWSE_PAYLOAD["data"][0])]
    payload["data"][0][7] = "X0.00"

    result = TWSEDailyMarketAdapter().parse(
        encoded(payload), DailyMarketRequest("2330", date(2025, 9, 1))
    )

    assert result.rows[0].price_change == Decimal("0.00")
    assert result.rows[0].price_direction == "X"


def test_adapter_rejects_changed_source_fields_instead_of_guessing() -> None:
    payload = dict(TPEX_PAYLOAD)
    payload["tables"] = [dict(TPEX_PAYLOAD["tables"][0])]
    payload["tables"][0]["fields"] = [
        field.replace("成交張數", "成交數量")
        for field in TPExDailyMarketAdapter.fields
    ]

    with pytest.raises(SourceDataError, match="fields changed") as error:
        TPExDailyMarketAdapter().parse(
            encoded(payload), DailyMarketRequest("6488", date(2025, 9, 1))
        )
    assert error.value.reason_code == "schema_mismatch"


def test_raw_store_is_content_addressed_and_idempotent(tmp_path) -> None:
    store = LocalRawArtifactStore(tmp_path / "raw")
    first = store.put(b"official source bytes")
    repeated = store.put(b"official source bytes")

    assert first.created is True
    assert repeated.created is False
    assert first.digest == repeated.digest
    assert first.digest in first.storage_uri
    assert (tmp_path / "raw" / first.digest[:2] / first.digest).read_bytes() == (
        b"official source bytes"
    )
    assert store.read(
        storage_uri=first.storage_uri,
        expected_digest=first.digest,
        expected_byte_size=first.byte_size,
    ) == b"official source bytes"


def test_raw_store_rejects_retained_bytes_that_fail_hash_validation(tmp_path) -> None:
    store = LocalRawArtifactStore(tmp_path / "raw")
    stored = store.put(b"original raw bytes")
    path = tmp_path / "raw" / stored.digest[:2] / stored.digest
    path.write_bytes(b"tampered raw bytes")

    with pytest.raises(RawArtifactIntegrityError, match="SHA-256"):
        store.read(
            storage_uri=stored.storage_uri,
            expected_digest=stored.digest,
            expected_byte_size=stored.byte_size,
        )
