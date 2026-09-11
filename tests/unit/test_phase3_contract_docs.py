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
