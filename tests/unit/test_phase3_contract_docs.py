import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_phase3_documents_every_daily_quote_disposition() -> None:
    contract = (ROOT / "docs" / "security_daily_market.md").read_text()
    for field in (
        "OHLC",
        "volume",
        "trade value",
        "trade count",
        "price change",
        "price direction",
        "bid_snapshot",
        "ask_snapshot",
        "last bid/ask price and volume",
        "pced_file",
        "pced_row",
        "pced_col",
    ):
        assert field in contract


def test_phase3_domain_package_has_no_cache_or_http_dependency() -> None:
    package = ROOT / "src" / "stock_data_center" / "market_data"
    source = "\n".join(path.read_text() for path in package.glob("*.py"))
    assert "redis" not in source.lower()
    assert "fastapi" not in source.lower()


def test_security_market_is_documented_as_effective_dated_state() -> None:
    contract = (ROOT / "docs" / "security_daily_market.md").read_text()
    assert "`security.security_code` is stable identity" in contract
    assert "`security_metadata_versions`" in contract
    assert "Market is not stored on this identity row" in contract
    assert "market filter" in contract

    inventory = json.loads(
        (ROOT / "docs" / "data_domain_inventory.json").read_text()
    )
    market_fields = [
        field
        for field in inventory["fields"]
        if field["legacy_field"] == "market"
    ]
    assert market_fields
    assert all(field["target"] != "security.market" for field in market_fields)
    security_market_fields = [
        field for field in market_fields if field["legacy_table"] != "market_indices"
    ]
    assert all(
        field["disposition"] == "observed"
        and field["target"] == "security_metadata_versions.market"
        for field in security_market_fields
    )
