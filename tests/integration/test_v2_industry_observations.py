"""Step 39-b: the exchanges' by-category quotes into `industry_observations`.

Acceptance (ROADMAP Step 39 "39-b"): one date's sweep asks every category, each
page is a fetch with its raw file, and each stock in `stocks` the pages list is
one row naming its page; a rerun asks nothing, a refetch appends only a changed
category; a date whose sweep failed or cannot be true writes nothing and is
asked again; a date off the calendar is not asked; rows cannot be changed. The
anchor of a closed span is the last whole-market file listing the stock, and
the OTC reconciliation dates follow the TPEx announcements.
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
from sqlalchemy.exc import DBAPIError

from stock_data_center.db.schema_v2 import (
    fetches,
    industry_changes,
    industry_observations,
    listings,
    stocks,
    trading_days,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.v2 import industry_observations as obs
from stock_data_center.v2.fetch_log import FetchRecord, record_fetch

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PAGES = FIXTURES / "v2" / "industry" / "observations"
NOW = datetime(2026, 9, 26, 6, 0, tzinfo=UTC)
BEFORE_OBSERVATIONS = "f1c5b643f4c7"


class Pages:
    """Answers each page from the fixtures, an empty page otherwise; records it."""

    def __init__(self, edit=None, fail: str | None = None) -> None:
        self.asked: list[str] = []
        self.edit = edit or {}
        self.fail = fail

    def __call__(self, url: str, params: dict) -> bytes:
        name = self._name(url, params)
        self.asked.append(name)
        if name == self.fail:
            raise OSError("connection reset")
        content = (PAGES / name).read_bytes() if (PAGES / name).exists() \
            else self._empty(url, params)
        return self.edit[name](content) if name in self.edit else content

    @staticmethod
    def _name(url: str, params: dict) -> str:
        exchange = "tpex" if url == obs.TPEX_URL else "twse"
        return f"{exchange}_{params['date'].replace('/', '')}_{params['type']}.json"

    @staticmethod
    def _empty(url: str, params: dict) -> bytes:
        if url == obs.TPEX_URL:
            day = date(*map(int, params["date"].split("/")))
            payload = json.loads((PAGES / "tpex_20240924_01.json").read_bytes())
            payload["date"] = day.strftime("%Y%m%d")
            payload["tables"][0]["date"] = f"{day.year - 1911}/{day:%m/%d}"
        else:
            day = date.fromisoformat(params["date"])
            payload = json.loads((PAGES / "twse_20200406_19.json").read_bytes())
            payload.update(date=params["date"], type=params["type"])
            payload["tables"][8]["title"] = \
                f"{day.year - 1911}年{day:%m}月{day:%d}日 每日收盤行情()"
        return json.dumps(payload, ensure_ascii=False).encode()


def _row(code: str) -> list[str]:
    return [code, code] + [""] * 15


def _with_rows(*codes: str):
    def edit(content: bytes) -> bytes:
        payload = json.loads(content)
        table = payload["tables"][0]
        table["data"] += [_row(code) for code in codes]
        table["totalCount"] = len(table["data"])
        table["category"] = "其他"
        return json.dumps(payload, ensure_ascii=False).encode()
    return edit


def _fetch(db: Connection, tmp_path, dataset="stocks", source="twse_isin", key="seed",
           content=b"seed") -> object:
    return record_fetch(db, FetchRecord(dataset, source, key, None, "unspecified", "t", "abc",
                                        NOW), content=content, status="succeeded",
                        store=LocalRawArtifactStore(tmp_path))


def _seed(db: Connection, tmp_path, codes, days) -> None:
    fetch_id = _fetch(db, tmp_path)
    db.execute(sa.insert(stocks), [{"stock_id": code, "name": code, "fetch_id": fetch_id}
                                   for code in codes])
    if days:
        db.execute(sa.insert(trading_days), [{"trade_date": day, "fetch_id": fetch_id}
                                             for day in days])


OTC_DAYS = [date(2022, 7, 1), date(2023, 7, 3)]
OTC_CODES = ["5903", "5904", "5905"]
SII_DAY = date(2025, 7, 11)
SII_CODES = ["2888", "2867", "2809"]


def _ingest(db, tmp_path, source="tpex_otc_quotes", days=None, http=None, **kwargs):
    return obs.ingest(db, source, days or OTC_DAYS, git_commit="abc", http=http or Pages(),
                      now=lambda: NOW, store=LocalRawArtifactStore(tmp_path),
                      pause=lambda: None, **kwargs)


def _rows(db, source="tpex_otc_quotes"):
    return db.execute(sa.select(industry_observations)
                      .where(industry_observations.c.source == source)
                      .order_by(industry_observations.c.stock_id,
                                industry_observations.c.trade_date,
                                industry_observations.c.recorded_at)).mappings().all()


def test_a_sweep_writes_each_stock_with_the_page_that_listed_it(db: Connection, tmp_path) -> None:
    _seed(db, tmp_path, OTC_CODES, OTC_DAYS)
    pages = Pages()
    report = _ingest(db, tmp_path, http=pages)
    assert report["quarantined"] == [] and report["dates"] == 2
    assert len(pages.asked) == 2 * len(obs.codes_to_ask("tpex_otc_quotes"))
    assert [(r["stock_id"], r["trade_date"], r["industry_code"]) for r in _rows(db)] == [
        ("5903", date(2022, 7, 1), "18"), ("5903", date(2023, 7, 3), "38"),
        ("5904", date(2022, 7, 1), "18"), ("5904", date(2023, 7, 3), "38"),
        ("5905", date(2022, 7, 1), "18")]  # 5905 is on no page of 2023-07-03 we answer
    assert report["appended"] == 5 and report["outside_universe"] > 0  # 9960 and others
    page = db.scalar(sa.select(fetches.c.id).where(
        fetches.c.resource_key == "tpex_otc_quotes:by-category:2022-07-01:18"))
    assert {r["fetch_id"] for r in _rows(db) if r["trade_date"] == date(2022, 7, 1)} == {page}
    logged = db.execute(sa.select(fetches.c.status, fetches.c.sha256, fetches.c.dataset)
                        .where(fetches.c.dataset == obs.DATASET)).all()
    assert len(logged) == len(pages.asked)
    assert all(status == "succeeded" and sha for status, sha, _ in logged)
    for _, sha, _ in logged:
        assert (tmp_path / sha.hex()[:2] / sha.hex()).exists()


def test_twse_pages_give_delisted_companies_their_last_category(db: Connection, tmp_path) -> None:
    _seed(db, tmp_path, SII_CODES, [SII_DAY])
    pages = Pages()
    report = _ingest(db, tmp_path, source="twse_mi_index", days=[SII_DAY], http=pages)
    assert report["quarantined"] == []
    assert len(pages.asked) == len(obs.codes_to_ask("twse_mi_index"))
    assert {(r["stock_id"], r["industry_code"]) for r in _rows(db, "twse_mi_index")} == {
        ("2888", "17"), ("2867", "17"), ("2809", "17")}


def test_a_rerun_asks_nothing_and_a_refetch_appends_only_a_changed_category(
        db: Connection, tmp_path) -> None:
    _seed(db, tmp_path, SII_CODES, [SII_DAY])
    _ingest(db, tmp_path, source="twse_mi_index", days=[SII_DAY])
    pages = Pages()
    report = _ingest(db, tmp_path, source="twse_mi_index", days=[SII_DAY], http=pages)
    assert pages.asked == [] and report["skipped"] == 1 and report.get("appended", 0) == 0
    report = _ingest(db, tmp_path, source="twse_mi_index", days=[SII_DAY], refetch=True)
    assert report["appended"] == 0 and report["unchanged"] == 3

    # TWSE rebuilds its history: a later answer may place a company elsewhere.
    def without_2888(content: bytes) -> bytes:
        payload = json.loads(content)
        payload["tables"][8]["data"] = [r for r in payload["tables"][8]["data"] if r[0] != "2888"]
        return json.dumps(payload, ensure_ascii=False).encode()

    def with_2888(content: bytes) -> bytes:
        payload = json.loads(content)
        payload["tables"][8]["title"] = "114年07月11日 每日收盤行情(其他)"
        payload["tables"][8]["data"] = [_row("2888")]
        return json.dumps(payload, ensure_ascii=False).encode()

    report = _ingest(db, tmp_path, source="twse_mi_index", days=[SII_DAY], refetch=True,
                     http=Pages(edit={"twse_20250711_17.json": without_2888,
                                      "twse_20250711_20.json": with_2888}))
    assert report["appended"] == 1 and report["unchanged"] == 2
    versions = [r for r in _rows(db, "twse_mi_index") if r["stock_id"] == "2888"]
    assert [r["industry_code"] for r in versions] == ["17", "20"]
    assert versions[0]["recorded_at"] < versions[1]["recorded_at"]


def test_a_failed_page_leaves_its_date_unwritten_and_asked_again(
        db: Connection, tmp_path) -> None:
    _seed(db, tmp_path, OTC_CODES, OTC_DAYS)
    pages = Pages(fail="tpex_20230703_38.json")
    report = _ingest(db, tmp_path, http=pages)
    assert report["failed"] == 1 and report["dates"] == 1
    assert pages.asked[-1] == "tpex_20230703_38.json"  # the sweep stops at the failure
    assert {r["trade_date"] for r in _rows(db)} == {date(2022, 7, 1)}
    assert db.scalar(sa.select(fetches.c.status).where(
        fetches.c.resource_key == "tpex_otc_quotes:by-category:2023-07-03:38")) == "failed"
    pages = Pages()
    report = _ingest(db, tmp_path, http=pages)
    assert report["skipped"] == 1 and report["dates"] == 1
    assert "tpex_20230703_38.json" in pages.asked and "tpex_20220701_18.json" not in pages.asked
    assert {r["trade_date"] for r in _rows(db)} == set(OTC_DAYS)


def test_a_stock_on_two_pages_quarantines_its_date(db: Connection, tmp_path) -> None:
    _seed(db, tmp_path, OTC_CODES, OTC_DAYS)
    report = _ingest(db, tmp_path, http=Pages(edit={"tpex_20230703_20.json": _with_rows("5903")}))
    assert report["quarantined"] == [(date(2023, 7, 3), "two_categories")]
    assert {r["trade_date"] for r in _rows(db)} == {date(2022, 7, 1)}
    statuses = dict(db.execute(sa.select(fetches.c.resource_key, fetches.c.status).where(
        fetches.c.resource_key.like("tpex_otc_quotes:by-category:2023-07-03:%"))).all())
    assert statuses["tpex_otc_quotes:by-category:2023-07-03:20"] == "quarantined"
    assert statuses["tpex_otc_quotes:by-category:2023-07-03:38"] == "quarantined"
    assert statuses["tpex_otc_quotes:by-category:2023-07-03:24"] == "succeeded"
    pages = Pages()
    _ingest(db, tmp_path, http=pages)
    assert "tpex_20230703_38.json" in pages.asked
    assert {r["trade_date"] for r in _rows(db)} == set(OTC_DAYS)


def test_an_unreadable_page_quarantines_its_date_not_the_run(db: Connection, tmp_path) -> None:
    _seed(db, tmp_path, OTC_CODES, OTC_DAYS)
    report = _ingest(db, tmp_path,
                     http=Pages(edit={"tpex_20220701_18.json": lambda content: b"<html>"}))
    assert report["quarantined"] == [(date(2022, 7, 1), "unrecognised_layout")]
    assert {r["trade_date"] for r in _rows(db)} == {date(2023, 7, 3)}
    assert db.scalar(sa.select(fetches.c.reason_code).where(
        fetches.c.resource_key == "tpex_otc_quotes:by-category:2022-07-01:18")) \
        == "unrecognised_layout"


def test_a_date_off_the_calendar_is_not_asked(db: Connection, tmp_path) -> None:
    _seed(db, tmp_path, OTC_CODES, OTC_DAYS)
    pages = Pages()
    # Both exchanges answer a Saturday with empty "ok" pages.
    report = _ingest(db, tmp_path, days=[date(2020, 1, 4)], http=pages)
    assert pages.asked == [] and report["off_calendar"] == [date(2020, 1, 4)]


def test_rows_cannot_be_changed_and_hold_a_category_code(db: Connection, tmp_path) -> None:
    _seed(db, tmp_path, OTC_CODES, OTC_DAYS)
    _ingest(db, tmp_path)
    fetch_id = _rows(db)[0]["fetch_id"]
    with pytest.raises(DBAPIError, match="append-only"), db.begin_nested():
        db.execute(sa.update(industry_observations).values(industry_code="20"))
    with pytest.raises(DBAPIError, match="industry_code_value"), db.begin_nested():
        db.execute(sa.insert(industry_observations).values(
            stock_id="5903", source="tpex_otc_quotes", trade_date=date(2024, 1, 2),
            industry_code="80", fetch_id=fetch_id))
    with pytest.raises(DBAPIError, match="source_value"), db.begin_nested():
        db.execute(sa.insert(industry_observations).values(
            stock_id="5903", source="twse_announcement", trade_date=date(2024, 1, 2),
            industry_code="20", fetch_id=fetch_id))


def test_the_downgrade_refuses_stored_observations(isolated_database_url, tmp_path) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.begin() as connection:
            _seed(connection, tmp_path, ["5903"], [])
            connection.execute(sa.insert(industry_observations).values(
                stock_id="5903", source="tpex_otc_quotes", trade_date=date(2022, 7, 1),
                industry_code="18", fetch_id=connection.scalar(sa.select(fetches.c.id))))
        with pytest.raises(DBAPIError, match="industry_observations"):
            command.downgrade(alembic_config(isolated_database_url), BEFORE_OBSERVATIONS)
        with engine.connect() as connection:
            assert connection.scalar(
                sa.select(sa.func.count()).select_from(industry_observations)) == 1
    finally:
        engine.dispose()


# ---------------------------------------------------------------- which dates


def _whole_market(db, tmp_path, day: date, fixture: str) -> None:
    _fetch(db, tmp_path, "daily_price", "tpex_otc_quotes",
           f"tpex_otc_quotes:daily-quotes:{day}", (FIXTURES / fixture).read_bytes())


def test_a_closed_span_is_anchored_on_the_last_file_listing_the_stock(
        db: Connection, tmp_path) -> None:
    # 3452 is in the 2020-01-02 file and not the 2020-04-30 one; 8287 and 6488 are in both.
    _seed(db, tmp_path, ["3452", "8287", "1333", "6488"], [date(2020, 1, 2), date(2020, 4, 30)])
    _whole_market(db, tmp_path, date(2020, 1, 2), "tpex_otc_quotes_20200102.json")
    _whole_market(db, tmp_path, date(2020, 4, 30), "tpex_otc_quotes_20200430.json")
    fetch_id = db.scalar(sa.select(fetches.c.id).limit(1))
    spans = [("3452", date(2020, 1, 13)), ("8287", date(2020, 5, 11)),
             ("6488", date(2020, 4, 30)),  # a file dated on the delisting day is after it
             ("1333", None)]
    db.execute(sa.insert(listings), [
        {"stock_id": code, "market": "otc", "delisted_on": off, "fetch_id": fetch_id,
         "delisted_fetch_id": fetch_id if off else None} for code, off in spans])
    found = {a.stock_id: a.last_quoted
             for a in obs.anchors(db, store=LocalRawArtifactStore(tmp_path))}
    assert found == {"3452": date(2020, 1, 2), "8287": date(2020, 4, 30),
                     "6488": date(2020, 1, 2)}


def test_a_span_never_quoted_in_the_window_has_no_anchor(db: Connection, tmp_path) -> None:
    _seed(db, tmp_path, ["9999"], [date(2020, 1, 2)])
    _whole_market(db, tmp_path, date(2020, 1, 2), "tpex_otc_quotes_20200102.json")
    fetch_id = db.scalar(sa.select(fetches.c.id).limit(1))
    db.execute(sa.insert(listings).values(stock_id="9999", market="otc",
                                          delisted_on=date(2020, 3, 2), fetch_id=fetch_id,
                                          delisted_fetch_id=fetch_id))
    assert [(a.stock_id, a.last_quoted) for a in
            obs.anchors(db, store=LocalRawArtifactStore(tmp_path))] == [("9999", None)]


def test_the_otc_reconciliation_dates_follow_the_announcements(db: Connection, tmp_path) -> None:
    days = [date(2020, 1, 2), date(2020, 5, 29), date(2020, 6, 1), date(2023, 6, 30),
            date(2023, 7, 3), date(2026, 9, 15)]
    _seed(db, tmp_path, ["3687", "5903"], days)
    fetch_id = db.scalar(sa.select(fetches.c.id).limit(1))
    db.execute(sa.insert(industry_changes), [
        {"stock_id": stock, "source": "tpex_announcement", "effective_date": day,
         "announced_on": announced, "document_number": str(day), "old_industry": old,
         "new_industry": new, "fetch_id": fetch_id}
        for stock, day, announced, old, new in [
            ("3687", date(2020, 6, 1), date(2020, 5, 19), "文化創意業", "電子商務"),
            ("5903", date(2023, 7, 3), date(2023, 5, 23), "貿易百貨", "居家生活")]])
    # 2020-01-02, the trading day before and the day of each effective date, the last day
    assert obs.reconciliation_dates(db) == days
