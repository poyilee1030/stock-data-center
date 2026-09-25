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
        "last bid/ask price and volume",
        "pced_file",
        "pced_row",
        "pced_col",
    ):
        assert field in contract


def test_the_universe_is_documented_with_its_listing_spans() -> None:
    """ADR-0026 and ADR-0028: identity in `stocks`, spans in `listings`, bias disclosed."""
    contract = (ROOT / "docs" / "security_daily_market.md").read_text()
    assert "`stock_id` is the identity everywhere" in contract
    assert "`stocks` is today's state, not history" in contract
    assert "`listings` holds when and where each traded" in contract
    assert "survivorship bias" in contract

    inventory = json.loads(
        (ROOT / "docs" / "data_domain_inventory.json").read_text()
    )
    market_fields = [
        field
        for field in inventory["fields"]
        if field["legacy_field"] == "market"
    ]
    assert market_fields
    stock_market_fields = [
        field for field in market_fields if field["legacy_table"] != "market_indices"
    ]
    assert all(
        field["disposition"] == "observed" and field["target"] == "listings.market"
        for field in stock_market_fields
    )
