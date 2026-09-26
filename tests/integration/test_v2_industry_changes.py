"""Step 39-a: industry reclassification announcements into `industry_changes`.

Acceptance (ROADMAP Step 39 "39-a"): every list, detail and attachment is a
fetch with its raw file; each change a company's category underwent is one row
naming its fetches; a rerun asks for no detail again and appends nothing; a
refetch of an unchanged notice appends nothing and a changed one appends a
revision; a key another notice holds is refused; a notice that cannot be true
is quarantined whole and asked again next time; rows cannot be changed.
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

from stock_data_center.db.schema_v2 import fetches, industry_changes, stocks
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.v2 import industry_changes as ic
from stock_data_center.v2.fetch_log import FetchRecord, record_fetch
from stock_data_center.v2.release_rules import (
    industry_announcement_available_from,
    industry_announcement_available_from_sql,
)

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "v2" / "industry"
NOW = datetime(2026, 9, 26, 6, 0, tzinfo=UTC)
TWSE_DETAILS = {  # list id -> fixture
    "A45713FAAC9F11EBB2DA005056BE380E": "twse_detail_1101802256.json",
    "346FAB95F87B11EDB2DA005056BE380E": "twse_detail_1121802250.json",
    "88BA9DDB9BA111F19A80005056BE3760": "twse_detail_1151802340.json",
    "E3FF1BAE659211E9A1D8005056BE380E": "twse_detail_1081801827.json",
}
REGULATION = "B4B04EB3CD4D11EDB2DA005056BE380E"


def _twse_list() -> bytes:
    """The live list, cut to the notices the fixtures hold and one amendment."""
    payload = json.loads((FIXTURES / "twse_list.json").read_bytes())
    payload["data"] = [row for row in payload["data"]
                       if row[4] in TWSE_DETAILS or row[4] == REGULATION]
    payload["total"] = len(payload["data"])
    return json.dumps(payload, ensure_ascii=False).encode()


def _empty_tpex_list(year: int) -> bytes:
    return json.dumps({"date": f"{year}0101~{year}1231", "stat": "ok", "tables": [
        {"fields": ic.TPEX_LIST_FIELDS, "data": [], "totalCount": 0}]}).encode()


class Source:
    """Answers each request from the fixtures and records it; `edit` rewrites a page."""

    def __init__(self, edit=None, fail: str | None = None) -> None:
        self.asked: list[str] = []
        self.edit = edit or {}
        self.fail = fail

    def __call__(self, method: str, url: str, form: dict | None) -> bytes:
        name = self._name(url, form)
        self.asked.append(name)
        if self.fail and self.fail == name:
            raise OSError("connection reset")
        content = _twse_list() if name == "twse_list" else (
            (FIXTURES / name).read_bytes() if (FIXTURES / name).exists()
            else _empty_tpex_list(int(name.split("_")[-1])))
        return self.edit[name](content) if name in self.edit else content

    @staticmethod
    def _name(url: str, form: dict | None) -> str:
        if url == ic.TWSE_LIST_URL:
            return "twse_list"
        if "announcement_detail" in url:
            return TWSE_DETAILS[url.split("id=")[1].split("&")[0]]
        if url == ic.TPEX_LIST_URL:
            year = form["startDate"][:4]
            return f"tpex_list_{year}.json" if (FIXTURES / f"tpex_list_{year}.json").exists() \
                else f"tpex_list_{year}"
        if url == ic.TPEX_DETAIL_URL:
            import base64
            return f"tpex_detail_{base64.b64decode(form['docId']).decode()}.json"
        return {"1121802250-1.pdf": "twse_1121802250-1.pdf"}.get(
            url.rsplit("/", 1)[1], "tpex_" + url.rsplit("/", 1)[1])


def _seed(db: Connection, tmp_path, codes) -> None:
    fetch_id = record_fetch(
        db, FetchRecord("stocks", "twse_isin", "seed", None, "unspecified", "t", "abc", NOW),
        content=b"isin", status="succeeded", store=LocalRawArtifactStore(tmp_path))
    db.execute(sa.insert(stocks), [{"stock_id": code, "name": code, "industry": None,
                                    "fetch_id": fetch_id} for code in codes])


TWSE_CODES = ["1443", "1453", "1456", "2241", "2429", "2459", "2614", "3450", "3669", "6165",
              "8499", "3130", "6689", "8454", "2442", "2424", "3054", "3708"]
# 6165 changed twice, in 2021 and in 2023; 3054 in 2023 and in 2026.
TWSE_ROWS = 20
TPEX_CODES = ["3085", "5903", "2718", "6123", "3521", "6187", "6240", "8476"]


def _ingest(db, tmp_path, source="twse_announcement", http=None, **kwargs):
    return ic.ingest(db, source, git_commit="abc", http=http or Source(), now=lambda: NOW,
                     store=LocalRawArtifactStore(tmp_path), pause=lambda: None, **kwargs)


def _rows(db, source="twse_announcement"):
    return db.execute(sa.select(industry_changes).where(industry_changes.c.source == source)
                      .order_by(industry_changes.c.stock_id, industry_changes.c.recorded_at)
                      ).mappings().all()


def test_every_change_is_written_with_its_fetches(db: Connection, tmp_path) -> None:
    _seed(db, tmp_path, TWSE_CODES)
    source = Source()
    report = _ingest(db, tmp_path, http=source)
    assert report["written"] and report["quarantined"] == []
    assert report["regulation"] == 1 and report["before_window"] == 1
    assert report["notices"] == 3 and report["changes"] == 11 + 47 + 1
    assert report["appended"] == TWSE_ROWS
    assert "1439" in report["outside_universe"]  # not seeded, so not stored
    rows = {row["stock_id"]: row for row in _rows(db)}
    it = rows["3130"]
    assert (it["effective_date"], it["announced_on"], it["old_industry"], it["new_industry"]) == (
        date(2023, 7, 3), date(2023, 5, 22), "資訊服務業", "數位雲端")
    assert it["document_number"] == "臺證上一字第1121802250號"
    assert it["attachment_fetch_id"] is not None
    assert rows["8499"]["attachment_fetch_id"] is None
    assert rows["8499"]["effective_date"] == date(2021, 6, 1)
    assert [(row["effective_date"], row["new_industry"]) for row in _rows(db)
            if row["stock_id"] == "3054"] == [(date(2023, 7, 3), "食品工業"),
                                              (date(2026, 9, 1), "電子通路業")]
    # every request is a fetch with its raw file on disk
    logged = db.execute(sa.select(fetches.c.resource_key, fetches.c.status, fetches.c.sha256,
                                  fetches.c.reason_code)
                        .where(fetches.c.dataset == ic.DATASET)).all()
    assert len(logged) == len(source.asked) == 1 + 4 + 1
    assert all(status == "succeeded" and sha for _, status, sha, _ in logged)
    assert {key: reason for key, _, _, reason in logged}[
        "twse_announcement:1081801827"] == "before_window"
    for _, _, sha, _ in logged:
        assert (tmp_path / sha.hex()[:2] / sha.hex()).exists()


def test_a_rerun_asks_for_no_detail_and_appends_nothing(db: Connection, tmp_path) -> None:
    _seed(db, tmp_path, TWSE_CODES)
    _ingest(db, tmp_path)
    source = Source()
    report = _ingest(db, tmp_path, http=source)
    assert source.asked == ["twse_list"]
    assert report["skipped"] == 4 and report.get("appended", 0) == 0


def test_a_refetch_appends_only_what_changed(db: Connection, tmp_path) -> None:
    _seed(db, tmp_path, TWSE_CODES)
    _ingest(db, tmp_path)
    report = _ingest(db, tmp_path, refetch=True)
    assert report["appended"] == 0 and report["unchanged"] == TWSE_ROWS

    def corrected(content: bytes) -> bytes:
        return content.decode().replace("電子通路業", "電子零組件業").encode()

    report = _ingest(db, tmp_path, refetch=True,
                     http=Source(edit={"twse_detail_1151802340.json": corrected}))
    assert report["appended"] == 1
    versions = [row for row in _rows(db) if row["stock_id"] == "3054"
                and row["effective_date"] == date(2026, 9, 1)]
    assert [row["new_industry"] for row in versions] == ["電子通路業", "電子零組件業"]
    assert versions[0]["recorded_at"] < versions[1]["recorded_at"]


def test_a_key_another_notice_holds_is_refused(db: Connection, tmp_path) -> None:
    _seed(db, tmp_path, TWSE_CODES)
    _ingest(db, tmp_path)
    counts = ic.write(db, "twse_announcement", [{
        "stock_id": "3054", "effective_date": date(2026, 9, 1), "announced_on": date(2026, 8, 20),
        "document_number": "臺證上一字第1151802399號", "old_industry": "食品工業",
        "new_industry": "電子零組件業", "fetch_id": _rows(db)[0]["fetch_id"],
        "attachment_fetch_id": None}])
    assert counts["conflicts"] == 1 and counts["appended"] == 0
    assert [row["document_number"] for row in _rows(db)
            if row["effective_date"] == date(2026, 9, 1)] == ["臺證上一字第1151802340號"]


def test_a_detail_naming_another_notice_is_quarantined(db: Connection, tmp_path) -> None:
    _seed(db, tmp_path, TWSE_CODES)

    def renumbered(content: bytes) -> bytes:
        return content.decode().replace("1151802340", "1151802399").encode()

    report = _ingest(db, tmp_path, http=Source(edit={"twse_detail_1151802340.json": renumbered}))
    assert report["quarantined"] == [("1151802340", "detail_mismatch")]


def test_a_notice_that_cannot_be_true_is_quarantined_and_asked_again(
        db: Connection, tmp_path) -> None:
    _seed(db, tmp_path, TWSE_CODES)

    def impossible(content: bytes) -> bytes:
        return content.decode().replace("「電子通路業」", "「雲端通路」").encode()

    report = _ingest(db, tmp_path, http=Source(edit={"twse_detail_1151802340.json": impossible}))
    assert report["quarantined"] == [("1151802340", "unknown_industry")]
    assert not [row for row in _rows(db) if row["effective_date"] == date(2026, 9, 1)]
    status = db.execute(sa.select(fetches.c.status, fetches.c.reason_code, fetches.c.sha256)
                        .where(fetches.c.resource_key == "twse_announcement:1151802340")).one()
    assert status.status == "quarantined" and status.sha256 is not None
    source = Source()
    report = _ingest(db, tmp_path, http=source)
    assert "twse_detail_1151802340.json" in source.asked
    assert report["appended"] == 1


def test_a_failed_list_writes_nothing(db: Connection, tmp_path) -> None:
    _seed(db, tmp_path, TWSE_CODES)
    report = _ingest(db, tmp_path, http=Source(fail="twse_list"))
    assert not report["written"]
    assert _rows(db) == []
    assert db.scalar(sa.select(fetches.c.status).where(fetches.c.dataset == ic.DATASET)) == "failed"


def test_tpex_reads_the_otc_attachment_and_skips_the_emerging_board(
        db: Connection, tmp_path) -> None:
    _seed(db, tmp_path, TPEX_CODES)
    report = _ingest(db, tmp_path, source="tpex_announcement")
    assert report["written"] and report["quarantined"] == []
    assert report["out_of_scope"] == 3  # two emerging-board notices and a pioneer-board one
    assert report["regulation"] == 1
    assert report["notices"] == 2 and report["changes"] == 56 + 11
    rows = {(row["stock_id"], row["effective_date"]): row
            for row in _rows(db, "tpex_announcement")}
    assert rows["5903", date(2023, 7, 3)]["old_industry"] == "貿易百貨"
    assert rows["8476", date(2023, 7, 3)]["new_industry"] == "綠能環保"
    assert rows["6187", date(2025, 6, 2)]["new_industry"] == "半導體業"
    attachments = dict(db.execute(sa.select(fetches.c.resource_key, fetches.c.reason_code).where(
        fetches.c.resource_key.like("tpex_announcement:11202011201:attachment:%"))).all())
    assert attachments == {"tpex_announcement:11202011201:attachment:1": None,
                           "tpex_announcement:11202011201:attachment:2": "another_board"}


def test_rows_cannot_be_changed(db: Connection, tmp_path) -> None:
    _seed(db, tmp_path, TWSE_CODES)
    _ingest(db, tmp_path)
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(sa.update(industry_changes).values(new_industry="其他業"))


def test_a_row_must_change_the_category(db: Connection, tmp_path) -> None:
    _seed(db, tmp_path, TWSE_CODES)
    _ingest(db, tmp_path)
    fetch_id = _rows(db)[0]["fetch_id"]
    with pytest.raises(DBAPIError):
        db.execute(sa.insert(industry_changes).values(
            stock_id="3130", source="twse_announcement", effective_date=date(2024, 1, 1),
            announced_on=date(2023, 12, 1), document_number="x", old_industry="其他業",
            new_industry="其他業", fetch_id=fetch_id))


def test_the_release_rule_agrees_in_python_and_sql(db: Connection) -> None:
    for day in (date(2023, 5, 22), date(2020, 12, 31), date(2024, 2, 29)):
        assert db.scalar(sa.select(industry_announcement_available_from_sql(sa.literal(day)))) \
            == industry_announcement_available_from(day)


def test_the_downgrade_refuses_stored_changes(isolated_database_url, tmp_path) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.begin() as connection:
            _seed(connection, tmp_path, ["3054"])
            fetch_id = connection.scalar(sa.select(fetches.c.id))
            connection.execute(sa.insert(industry_changes).values(
                stock_id="3054", source="twse_announcement", effective_date=date(2026, 9, 1),
                announced_on=date(2026, 8, 19), document_number="n", old_industry="食品工業",
                new_industry="電子通路業", fetch_id=fetch_id))
        with pytest.raises(DBAPIError, match="industry_changes"):
            command.downgrade(alembic_config(isolated_database_url), "-1")
        with engine.connect() as connection:
            assert connection.scalar(
                sa.select(sa.func.count()).select_from(industry_changes)) == 1
    finally:
        engine.dispose()
