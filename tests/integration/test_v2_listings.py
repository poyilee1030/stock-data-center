"""Step 38-a: every company's listing spans, and the adapters' scope.

Acceptance (ROADMAP Step 38-a): the refresh keeps every raw page and logs every
fetch; a rerun changes nothing; 5236 has its two spans; adding delisted
companies leaves every adapter's requests unchanged; nothing's category is
guessed from its code; the migration moves today's market and listing date
into open spans, and its downgrade refuses what it cannot represent.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from conftest import alembic_config
from sqlalchemy import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError

from stock_data_center.db.schema_v2 import listings, stocks
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.v2 import listings as ls
from stock_data_center.v2.fetch_log import FetchRecord, record_fetch

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "v2" / "listings"
NOW = datetime(2026, 9, 25, 6, 0, tzinfo=UTC)


def _empty_tpex(year: int, kind: str) -> bytes:
    fields = (["索引", "股票代號", "公司名稱", "上櫃日期", "每股面額", "公司資訊連結",
               "近期上櫃資訊連結"] if kind == "latest" else
              ["股票代號", "公司名稱", "終止上櫃日期", "終止上櫃原因", "公司資料網址"])
    return json.dumps({"date": str(year), "stat": "ok", "tables": [
        {"fields": fields, "data": [], "totalCount": 0, "title": "", "notes": []}]},
        ensure_ascii=False).encode()


class Pages:
    """Answers each URL the refresh asks for from the fixtures, and records it."""

    def __init__(self, fail: str | None = None) -> None:
        self.asked: list[str] = []
        self.fail = fail

    def __call__(self, url: str) -> bytes:
        self.asked.append(url)
        if self.fail and self.fail in url:
            raise OSError("connection reset")
        if "C_public.jsp" in url:
            market = "sii" if url.endswith("strMode=2") else "otc"
            return (FIXTURES / f"isin_{market}_excerpt.html").read_bytes()
        if "newlisting" in url:
            return (FIXTURES / "twse_newlisting_excerpt.json").read_bytes()
        if "suspendListing" in url:
            return (FIXTURES / "twse_suspendlisting_excerpt.json").read_bytes()
        if "class_main.jsp" in url:
            code = url.split("owncode=")[1].split("&")[0]
            page = FIXTURES / f"isin_lookup_{code}.html"
            return (page if page.exists() else FIXTURES / "isin_lookup_1701.html").read_bytes()
        kind = "latest" if "/company/latest" in url else "deListed"
        year = int(url.split("date=")[1].split("&")[0])
        if kind == "deListed" and year == 2020:  # served ten rows at a time
            name = "all" if "paging-size=" in url else "page1"
            return (FIXTURES / f"tpex_deListed_2020_{name}.json").read_bytes()
        page = FIXTURES / f"tpex_{kind}_{year}_excerpt.json"
        return page.read_bytes() if page.exists() else _empty_tpex(year, kind)


def _refresh(db, tmp_path, pages=None):
    return ls.refresh(db, git_commit="abc", get=pages or Pages(), now=lambda: NOW,
                      store=LocalRawArtifactStore(tmp_path), pause=lambda: None)


def _spans(db, stock_id):
    return db.execute(
        sa.select(listings.c.market, listings.c.listed_on, listings.c.delisted_on)
        .where(listings.c.stock_id == stock_id).order_by(listings.c.listed_on.nulls_first())
    ).all()


def _fetch(db, tmp_path):
    return record_fetch(
        db, FetchRecord("stocks", "twse_isin", "t", None, "unspecified", "t", "abc", NOW),
        content=b"isin", status="succeeded", store=LocalRawArtifactStore(tmp_path))


# ---------------------------------------------------------------- refresh


def test_the_refresh_keeps_every_page_and_fills_both_tables(db: Connection, tmp_path) -> None:
    pages = Pages()
    report = _refresh(db, tmp_path, pages)
    assert report["written"] is True
    assert _spans(db, "5236") == [("otc", date(2021, 7, 29), date(2026, 7, 16)),
                                  ("sii", date(2026, 7, 16), None)]
    assert _spans(db, "2809") == [("sii", None, date(2025, 10, 1))]
    assert db.scalar(sa.select(stocks.c.name).where(stocks.c.stock_id == "2809")) == "京城銀"
    assert not db.scalar(sa.select(sa.func.count()).select_from(stocks)
                         .where(stocks.c.stock_id.in_(["1701", "9188", "912398"])))
    # One fetch per page asked, each with its raw file.
    logged = db.execute(sa.text(
        "SELECT source, status, sha256 IS NOT NULL FROM fetches")).all()
    assert len(logged) == len(pages.asked)
    assert {status for _, status, _ in logged} == {"succeeded"}
    assert all(has_raw for *_, has_raw in logged)
    assert {source for source, *_ in logged} == {
        "twse_isin", "twse_newlisting", "twse_suspendlisting", "tpex_latest",
        "tpex_delisted", "twse_isin_lookup"}
    # TPEx from its first listing year to this year, both tables, and the rest
    # of 2020's delistings, which come ten to a page.
    tpex = [u for u in pages.asked if "tpex.org.tw" in u]
    assert len(tpex) == 2 * (2026 - ls.FIRST_TPEX_YEAR + 1) + 1
    assert [u for u in tpex if "paging-size=12" in u]
    assert ("3452", "otc", "unproven_category") in {
        tuple(q[:3]) for q in report["quarantined_spans"]}
    assert report["quarantined"]["unproven_category"] >= 3


def test_a_second_refresh_changes_nothing(db: Connection, tmp_path) -> None:
    _refresh(db, tmp_path)
    before = db.execute(sa.select(listings).order_by(listings.c.stock_id,
                                                     listings.c.market)).all()
    names = db.execute(sa.select(stocks.c.stock_id, stocks.c.name)
                       .order_by(stocks.c.stock_id)).all()
    _refresh(db, tmp_path)
    after = db.execute(sa.select(listings).order_by(listings.c.stock_id,
                                                    listings.c.market)).all()
    strip = [(r.stock_id, r.market, r.listed_on, r.delisted_on) for r in before]
    assert strip == [(r.stock_id, r.market, r.listed_on, r.delisted_on) for r in after]
    assert names == db.execute(sa.select(stocks.c.stock_id, stocks.c.name)
                               .order_by(stocks.c.stock_id)).all()


def test_a_failed_page_writes_nothing(db: Connection, tmp_path) -> None:
    _refresh(db, tmp_path)
    spans = db.scalar(sa.select(sa.func.count()).select_from(listings))
    report = _refresh(db, tmp_path, Pages(fail="deListed?response=json&date=2011"))
    assert report["written"] is False and "tpex_delisted" in report["reason"]
    assert db.scalar(sa.select(sa.func.count()).select_from(listings)) == spans
    failed = db.execute(sa.text(
        "SELECT status FROM fetches WHERE source = 'tpex_delisted' AND status <> 'succeeded'"
    )).scalars().all()
    assert failed == ["failed"]


def test_a_page_that_does_not_parse_is_kept_and_quarantined(db: Connection, tmp_path) -> None:
    class Broken(Pages):
        def __call__(self, url):
            body = super().__call__(url)
            return b'{"stat": "OK", "fields": ["x"], "data": []}' if "newlisting" in url else body

    report = _refresh(db, tmp_path, Broken())
    assert report["written"] is False
    row = db.execute(sa.text(
        "SELECT status, sha256 IS NOT NULL FROM fetches WHERE source = 'twse_newlisting'"
    )).one()
    assert tuple(row) == ("quarantined", True)


# ---------------------------------------------------------------- scope


def test_the_adapters_read_only_open_spans(db: Connection, tmp_path) -> None:
    _refresh(db, tmp_path)
    assert ls.listed_stock_ids(db) == {"2330", "5236", "6757", "6423", "3718"}


def test_every_adapter_asks_for_its_stocks_through_the_open_spans() -> None:
    # A call site reading `stocks` directly would widen its requests the moment
    # a delisted company is added. These are every reader of the scope.
    root = Path(__file__).resolve().parents[2] / "src" / "stock_data_center"
    readers = ["v2/backfill.py", "v2/exchange_daily.py", "v2/corporate_actions.py",
               "v2/financial_reports.py"]
    for path in readers:
        text = (root / path).read_text()
        assert "listed_stock_ids(" in text, path
        assert "select(stocks.c.stock_id)" not in text, path
        assert "select(v2.stocks.c.stock_id)" not in text, path


def test_a_delisted_company_is_out_of_the_exchange_daily_scope(
    db: Connection, tmp_path
) -> None:
    from stock_data_center.v2 import exchange_daily as xd

    fetch_id = _fetch(db, tmp_path)
    for stock_id, delisted in (("2330", None), ("2317", date(2026, 9, 1))):
        db.execute(sa.insert(stocks).values(stock_id=stock_id, name=stock_id,
                                            fetch_id=fetch_id))
        db.execute(sa.insert(listings).values(
            stock_id=stock_id, market="sii", delisted_on=delisted, fetch_id=fetch_id,
            delisted_fetch_id=fetch_id if delisted else None))
    mi_index = (Path(__file__).resolve().parents[1] / "fixtures"
                / "twse_mi_index_allbut0999_20260911.json").read_bytes()

    class One:
        def fetch(self, resource):
            from stock_data_center.ingestion.models import FetchedArtifact
            return FetchedArtifact(content=mi_index, source_uri=resource.source_uri,
                                   fetched_at=NOW, media_type="application/json")

    outcome = xd.ingest(db, xd.JOBS["daily_prices/twse_mi_index"], date(2026, 9, 11),
                        fetcher=One(), git_commit="abc", purpose="gap_fill",
                        store=LocalRawArtifactStore(tmp_path))
    written = db.execute(sa.text("SELECT DISTINCT stock_id FROM daily_prices")).scalars()
    assert set(written) == {"2330"}
    assert outcome.appended == 1


# ---------------------------------------------------------------- the table


def test_a_stock_has_at_most_one_open_span(db: Connection, tmp_path) -> None:
    fetch_id = _fetch(db, tmp_path)
    db.execute(sa.insert(stocks).values(stock_id="5236", name="凌陽創新", fetch_id=fetch_id))
    db.execute(sa.insert(listings).values(stock_id="5236", market="sii", fetch_id=fetch_id))
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(sa.insert(listings).values(stock_id="5236", market="otc",
                                              fetch_id=fetch_id))


@pytest.mark.parametrize("values", [
    {"listed_on": date(2026, 7, 16), "delisted_on": date(2026, 7, 16)},  # empty span
    {"listed_on": date(2021, 7, 29)},  # a date with no fetch behind it
    {"delisted_on": date(2026, 7, 16)},
    {"market": "pub"},
])
def test_a_span_must_be_well_formed(db: Connection, tmp_path, values) -> None:
    fetch_id = _fetch(db, tmp_path)
    db.execute(sa.insert(stocks).values(stock_id="5236", name="凌陽創新", fetch_id=fetch_id))
    row = {"stock_id": "5236", "market": "otc", "fetch_id": fetch_id, **values}
    if "listed_on" in values and "delisted_on" in values:
        row.update(listed_fetch_id=fetch_id, delisted_fetch_id=fetch_id)
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(sa.insert(listings).values(**row))


# ---------------------------------------------------------------- migration


def test_the_migration_turns_each_stock_into_an_open_span(isolated_database_url, tmp_path):
    config = alembic_config(isolated_database_url)
    command.downgrade(config, "-1")
    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.begin() as connection:
            fetch_id = _fetch(connection, tmp_path)
            connection.execute(sa.text(
                "INSERT INTO stocks (stock_id, name, market, industry, listed_on, fetch_id) "
                "VALUES ('2330', '台積電', 'sii', '半導體業', '1994-09-05', :f), "
                "       ('6488', '環球晶', 'otc', NULL, NULL, :f)"), {"f": fetch_id})
        command.upgrade(config, "head")
        with engine.connect() as connection:
            rows = connection.execute(sa.text(
                "SELECT stock_id, market, listed_on, delisted_on, "
                "listed_fetch_id IS NOT NULL, fetch_id = :f FROM listings ORDER BY stock_id"),
                {"f": fetch_id}).all()
            assert [tuple(r) for r in rows] == [
                ("2330", "sii", date(1994, 9, 5), None, True, True),
                ("6488", "otc", None, None, False, True)]
            columns = {c["name"] for c in sa.inspect(connection).get_columns("stocks")}
            assert columns == {"stock_id", "name", "industry", "fetch_id"}
        # Open spans alone go back into stocks.
        command.downgrade(config, "-1")
        with engine.connect() as connection:
            back = connection.execute(sa.text(
                "SELECT stock_id, market, listed_on FROM stocks ORDER BY stock_id")).all()
            assert [tuple(r) for r in back] == [("2330", "sii", date(1994, 9, 5)),
                                                ("6488", "otc", None)]
    finally:
        engine.dispose()


def test_the_downgrade_refuses_a_closed_span(isolated_database_url, tmp_path) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.begin() as connection:
            fetch_id = _fetch(connection, tmp_path)
            connection.execute(sa.insert(stocks).values(stock_id="2809", name="京城銀",
                                                        fetch_id=fetch_id))
            connection.execute(sa.insert(listings).values(
                stock_id="2809", market="sii", delisted_on=date(2025, 10, 1),
                fetch_id=fetch_id, delisted_fetch_id=fetch_id))
        with pytest.raises(DBAPIError, match="closed listing span"):
            command.downgrade(alembic_config(isolated_database_url), "-1")
        with engine.connect() as connection:
            assert connection.scalar(sa.select(sa.func.count()).select_from(listings)) == 1
    finally:
        engine.dispose()


def test_an_isin_page_that_no_longer_parses_is_kept_and_nothing_is_written(
    db: Connection, tmp_path
) -> None:
    class ChangedLayout(Pages):
        def __call__(self, url):
            body = super().__call__(url)
            if "C_public.jsp" not in url:
                return body
            return body.decode("big5hkscs").replace("<B> 股票 <B>", "<B> 普通股 <B>").encode(
                "big5hkscs")

    report = _refresh(db, tmp_path, ChangedLayout())
    assert report["written"] is False
    fetched = db.execute(sa.text(
        "SELECT status, reason_code, length(sha256) FROM fetches WHERE dataset = 'stocks'")).all()
    assert fetched == [("quarantined", "unrecognised_layout", 32)]  # it stops at the first
    assert db.scalar(sa.text("SELECT count(*) FROM stocks")) == 0


def test_the_isin_list_date_is_the_taipei_date(db: Connection, tmp_path) -> None:
    """17:00 UTC on the 24th is 01:00 on the 25th in Taipei."""
    late = datetime(2026, 9, 24, 17, 0, tzinfo=UTC)
    ls.refresh(db, git_commit="abc", get=Pages(), now=lambda: late,
               store=LocalRawArtifactStore(tmp_path), pause=lambda: None)
    keys = db.scalars(sa.text(
        "SELECT resource_key FROM fetches WHERE dataset = 'stocks' ORDER BY 1")).all()
    assert keys == ["twse_isin:otc:2026-09-25", "twse_isin:sii:2026-09-25"]
