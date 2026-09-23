"""Step 22-c — what the legacy archive proves about when revenue was public.

The archive is read in two windows, because it holds two different things
(audit §7.1, §7.4):

* **2020M01–2026M01** — `revswarm` recovered the announcement *date* and wrote
  it into `market.csv`; the first-published *value* is not recoverable. So the
  window contributes evidence only, attached to the official version we
  already hold: `press_report_bound` at the end of the recovered day, or, for a
  row left on the statutory 10th (indistinguishable from the fallback by value
  alone), the release rule.
* **2026M02 onward** — the dates are the legacy 22:45 job's real first-seen
  dates, and the values are what it captured then. Those rows import as
  observations with `legacy_capture_bound`, so a row corrected afterwards
  resolves to what was public at the time, and the correction stays invisible
  until something captures it.

KY issuers are in neither window: legacy hard-coded the `_0` URL, so the
archive has no foreign issuer at all. They keep `unknown` until Step 28's
forward capture proves one (owner decision, 2026-09-20).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa

from stock_data_center.ingestion.adapters import (
    LegacyMonthlyRevenueArchiveAdapter,
    MOPSOtcMonthlyRevenueAdapter,
    MOPSSiiMonthlyRevenueAdapter,
)
from stock_data_center.ingestion.models import (
    FetchedArtifact,
    MonthlyRevenueArchiveRequest,
    MonthlyRevenueRequest,
    RevenuePage,
)
from stock_data_center.ingestion.monthly_revenue import MonthlyRevenueImporter
from stock_data_center.ingestion.monthly_revenue_archive import (
    MonthlyRevenueArchiveImporter,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.market_calendar import (
    TradingCalendarObservation,
    TradingCalendarWriter,
)
from stock_data_center.market_calendar.models import CalendarLineageRef
from stock_data_center.monthly_revenue import MonthlyRevenueService
from stock_data_center.monthly_revenue.models import RevenuePeriod
from stock_data_center.pit import MarketPITContext
from stock_data_center.provenance import IngestPurpose

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SII_PAGE = (FIXTURES / "mops_t21sc03_sii_115_7_0.html").read_bytes()
SII_KY_PAGE = (FIXTURES / "mops_t21sc03_sii_115_7_1.html").read_bytes()
OTC_JUNE_PAGE = (FIXTURES / "mops_t21sc03_otc_115_6_0.html").read_bytes()
ARCHIVE_A = (FIXTURES / "legacy_market_202103.csv").read_bytes()
ARCHIVE_B = (FIXTURES / "legacy_market_202606.csv").read_bytes()
ARCHIVE_B_SII = (FIXTURES / "legacy_market_202607.csv").read_bytes()

MARCH = RevenuePeriod(2021, 3)
JUNE = RevenuePeriod(2026, 6)
JULY = RevenuePeriod(2026, 7)
TAIPEI = ZoneInfo("Asia/Taipei")
PREVIOUS_HEAD = "b7e4c1a95d38"

# 2021-04-10 is a Saturday, so the statutory rule moves to Monday 2021-04-12.
APRIL_TRADING_DAYS = (
    date(2021, 4, 1), date(2021, 4, 6), date(2021, 4, 7), date(2021, 4, 8),
    date(2021, 4, 9), date(2021, 4, 12), date(2021, 4, 13),
)


def end_of_day(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=TAIPEI)


class StaticFetcher:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def fetch(self, resource):
        return FetchedArtifact(
            content=self.content,
            source_uri=resource.source_uri,
            fetched_at=datetime.now(UTC),
            media_type="text/html",
        )


def restamped(content: bytes, period: RevenuePeriod) -> bytes:
    """The captured page retitled, so one fixture can stand in for any month."""
    import re

    text = content.decode("cp950")
    return re.sub(
        r"(\d+)年(\d+)月份", f"{period.year - 1911}年{period.month}月份", text, count=1
    ).encode("cp950")


def import_official(engine, tmp_path, *, adapter, content, period,
                    page=RevenuePage.DOMESTIC, purpose=IngestPurpose.GAP_FILL):
    importer = MonthlyRevenueImporter(
        engine,
        raw_store=LocalRawArtifactStore(tmp_path / "raw"),
        fetcher=StaticFetcher(restamped(content, period)),
    )
    return importer.run(
        adapter=adapter,
        request=MonthlyRevenueRequest(period, page),
        import_id=uuid4(),
        purpose=purpose,
    )


def archive_root(tmp_path: Path, *, month: RevenuePeriod, content: bytes) -> Path:
    root = tmp_path / "legacy"
    folder = root / f"{month.year}" / f"{month.year}M{month.month:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "market.csv").write_bytes(content)
    return root


def import_archive(engine, tmp_path, *, source, period, root, import_id=None):
    importer = MonthlyRevenueArchiveImporter(
        engine, raw_store=LocalRawArtifactStore(tmp_path / "raw")
    )
    import_id = import_id or uuid4()
    result = importer.run(
        adapter=LegacyMonthlyRevenueArchiveAdapter(source, archive_root=root),
        request=MonthlyRevenueArchiveRequest(period),
        import_id=import_id,
        purpose=IngestPurpose.GAP_FILL,
    )
    with engine.connect() as connection:
        manifest = importer.manifest(connection, import_id)
    return result, manifest


def store_calendar(connection, *, month: date, days, through: date) -> None:
    run_id = connection.scalar(
        sa.text(
            "INSERT INTO ingest_runs (dataset_code, source, status, started_at, "
            " purpose) VALUES ('trading_calendar', 'twse', 'succeeded', now(), "
            "'gap_fill') RETURNING id"
        )
    )
    artifact_id = connection.scalar(
        sa.text(
            "INSERT INTO raw_artifacts (raw_artifact_hash, storage_uri, byte_size, "
            " media_type) VALUES (:h, :uri, 1, 'application/json') RETURNING id"
        ),
        {"h": "b" * 64, "uri": f"file://{'b' * 64}"},
    )
    connection.execute(
        sa.text(
            "INSERT INTO raw_artifact_observations (raw_artifact_id, ingest_run_id, "
            " source_uri, fetched_at, artifact_origin) "
            "VALUES (:a, :r, 'https://x', now(), 'official_fetch')"
        ),
        {"a": artifact_id, "r": run_id},
    )
    TradingCalendarWriter().append_month(
        connection,
        source="twse",
        observation=TradingCalendarObservation(
            market="TWSE",
            calendar_month=month,
            trading_days=tuple(days),
            coverage_through=through,
        ),
        lineage=CalendarLineageRef(artifact_id, run_id),
    )
    connection.commit()


def claimed_types(rows) -> list[str]:
    """What the archive claimed, leaving out the `unknown` row 22-b wrote.

    Every version already carries one: the official import proved nothing, so
    it recorded ignorance. The archive's evidence outranks it rather than
    replacing it — storage is append-only.
    """
    return [row["evidence_type"] for row in rows if row["evidence_type"] != "official"]


def evidence_rows(connection, code: str, period: RevenuePeriod, source: str):
    return connection.execute(
        sa.text(
            """
            SELECT e.evidence_type, e.published_at, e.quality_rank,
                   e.evidence_source, v.revenue
              FROM publication_evidence e
              JOIN monthly_revenue_versions v
                ON v.id = e.monthly_revenue_version_id
              JOIN security s ON s.id = v.security_id
             WHERE s.security_code = :code AND v.source = :source
               AND v.revenue_year = :year AND v.revenue_month = :month
             ORDER BY e.quality_rank DESC, e.id
            """
        ),
        {
            "code": code, "source": source,
            "year": period.year, "month": period.month,
        },
    ).mappings().all()


def market(moment: datetime) -> MarketPITContext:
    """What the market knew at `moment`, using everything we have recorded.

    `knowledge_as_of` is now, not `moment`: the evidence was recorded today,
    and asking what we knew in 2021 would answer nothing for every row.
    """
    return MarketPITContext(
        information_as_of=moment,
        knowledge_as_of=datetime.now(UTC) + timedelta(minutes=1),
    )


def resolved(connection, code, period, source, moment):
    return MonthlyRevenueService().revenue(
        connection,
        security_code=code,
        period=period,
        context=market(moment),
        source=source,
    )


def test_a_recovered_announcement_date_becomes_press_report_evidence(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        import_official(
            engine, tmp_path, adapter=MOPSSiiMonthlyRevenueAdapter(),
            content=SII_PAGE, period=MARCH,
        )
        root = archive_root(tmp_path, month=MARCH, content=ARCHIVE_A)
        result, manifest = import_archive(
            engine, tmp_path, source="mops_t21sc03_sii", period=MARCH, root=root
        )
        assert manifest.status == "succeeded"
        assert result.business_versions_created == 0
        with engine.connect() as connection:
            rows = evidence_rows(connection, "2330", MARCH, "mops_t21sc03_sii")
        assert claimed_types(rows) == ["press_report_bound"]
        assert rows[0]["published_at"] == end_of_day(date(2021, 4, 9))
        assert rows[0]["quality_rank"] == 60
    finally:
        engine.dispose()


def test_market_pit_sees_it_at_the_announcement_and_not_before(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        import_official(
            engine, tmp_path, adapter=MOPSSiiMonthlyRevenueAdapter(),
            content=SII_PAGE, period=MARCH,
        )
        root = archive_root(tmp_path, month=MARCH, content=ARCHIVE_A)
        import_archive(
            engine, tmp_path, source="mops_t21sc03_sii", period=MARCH, root=root
        )
        announced = end_of_day(date(2021, 4, 9))
        with engine.connect() as connection:
            assert resolved(
                connection, "2330", MARCH, "mops_t21sc03_sii",
                announced - timedelta(seconds=1),
            ) is None
            assert resolved(
                connection, "2330", MARCH, "mops_t21sc03_sii", announced
            ) is not None
    finally:
        engine.dispose()


def test_a_row_left_on_the_statutory_tenth_resolves_by_the_rule(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """2021-04-10 was a Saturday, so the deadline moves to Monday the 12th."""
    engine = sa.create_engine(isolated_database_url)
    try:
        with engine.connect() as connection:
            store_calendar(
                connection, month=date(2021, 4, 1), days=APRIL_TRADING_DAYS,
                through=date(2021, 4, 30),
            )
        import_official(
            engine, tmp_path, adapter=MOPSOtcMonthlyRevenueAdapter(),
            content=OTC_JUNE_PAGE, period=MARCH,
        )
        root = archive_root(tmp_path, month=MARCH, content=ARCHIVE_A)
        import_archive(
            engine, tmp_path, source="mops_t21sc03_otc", period=MARCH, root=root
        )
        with engine.connect() as connection:
            rows = evidence_rows(connection, "6488", MARCH, "mops_t21sc03_otc")
            assert claimed_types(rows) == ["release_rule"]
            assert rows[0]["published_at"] == end_of_day(date(2021, 4, 12))
            assert rows[0]["evidence_source"] == "monthly_revenue_statutory@1"
            assert resolved(
                connection, "6488", MARCH, "mops_t21sc03_otc",
                end_of_day(date(2021, 4, 10)),
            ) is None
    finally:
        engine.dispose()


def test_a_first_captured_value_is_its_own_version_with_a_capture_bound(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """6441 廣錠 2026M06: legacy captured 13,094 千元, MOPS now serves 10,948."""
    engine = sa.create_engine(isolated_database_url)
    try:
        import_official(
            engine, tmp_path, adapter=MOPSOtcMonthlyRevenueAdapter(),
            content=OTC_JUNE_PAGE, period=JUNE,
        )
        root = archive_root(tmp_path, month=JUNE, content=ARCHIVE_B)
        result, _ = import_archive(
            engine, tmp_path, source="mops_t21sc03_otc", period=JUNE, root=root
        )
        assert result.business_versions_created == 1
        with engine.connect() as connection:
            rows = evidence_rows(connection, "6441", JUNE, "mops_t21sc03_otc")
        captured = [row for row in rows if row["evidence_type"] == "legacy_capture_bound"]
        assert len(captured) == 1
        assert captured[0]["revenue"] == Decimal(13094) * 1000
        assert captured[0]["published_at"] == end_of_day(date(2026, 7, 6))
        assert captured[0]["quality_rank"] == 70
    finally:
        engine.dispose()


def test_the_correction_stays_invisible_until_something_captures_it(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """Market PIT answers what was public, and a correction waits for proof.

    Step 22-b imported today's page as `gap_fill`, which proves nothing about
    when its value became public (ADR-0020 §2), so the corrected 10,948 has no
    instant to be visible from. The first-captured 13,094 does, and it is what
    the market had. A run that is the first to see a *further* correction
    proves that one, and Market PIT moves to it from that instant on — never
    before it.
    """
    engine = sa.create_engine(isolated_database_url)
    try:
        import_official(
            engine, tmp_path, adapter=MOPSOtcMonthlyRevenueAdapter(),
            content=OTC_JUNE_PAGE, period=JUNE,
        )
        root = archive_root(tmp_path, month=JUNE, content=ARCHIVE_B)
        import_archive(
            engine, tmp_path, source="mops_t21sc03_otc", period=JUNE, root=root
        )
        later = datetime.now(UTC) + timedelta(minutes=1)
        with engine.connect() as connection:
            record = resolved(connection, "6441", JUNE, "mops_t21sc03_otc", later)
        assert Decimal(record.data["revenue"]) == Decimal(13094) * 1000

        before_capture = datetime.now(UTC)
        corrected = OTC_JUNE_PAGE.decode("cp950").replace(
            ">6441</td><td align=left>廣錠</td><td nowrap>                 10,948",
            ">6441</td><td align=left>廣錠</td><td nowrap>                 11,500",
        ).encode("cp950")
        import_official(
            engine, tmp_path, adapter=MOPSOtcMonthlyRevenueAdapter(),
            content=corrected, period=JUNE,
            purpose=IngestPurpose.CORRECTION_CHECK,
        )
        after = datetime.now(UTC) + timedelta(minutes=1)
        with engine.connect() as connection:
            assert Decimal(
                resolved(connection, "6441", JUNE, "mops_t21sc03_otc", after)
                .data["revenue"]
            ) == Decimal(11500) * 1000
            assert Decimal(
                resolved(
                    connection, "6441", JUNE, "mops_t21sc03_otc", before_capture
                ).data["revenue"]
            ) == Decimal(13094) * 1000
    finally:
        engine.dispose()


def test_a_legacy_row_equal_to_the_official_one_adds_no_version(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        import_official(
            engine, tmp_path, adapter=MOPSOtcMonthlyRevenueAdapter(),
            content=OTC_JUNE_PAGE, period=JUNE,
        )
        root = archive_root(tmp_path, month=JUNE, content=ARCHIVE_B)
        import_archive(
            engine, tmp_path, source="mops_t21sc03_otc", period=JUNE, root=root
        )
        with engine.connect() as connection:
            versions = connection.scalar(
                sa.text(
                    "SELECT count(*) FROM monthly_revenue_versions v "
                    "JOIN security s ON s.id = v.security_id "
                    "WHERE s.security_code = '6488' AND v.revenue_year = 2026 "
                    "AND v.revenue_month = 6"
                )
            )
            rows = evidence_rows(connection, "6488", JUNE, "mops_t21sc03_otc")
        assert versions == 1
        assert claimed_types(rows) == ["legacy_capture_bound"]
    finally:
        engine.dispose()


def test_a_note_legacy_mangled_is_not_a_new_version(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """2025 2026M06: legacy's 不�袗� is the page's 不銹鋼, re-encoded badly.

    Every other field matches, so the row is the same published value read
    through a broken decoder (Step 22-b, audit §4.7). Forking a version on our
    own capture defect would make the mangled text the Market-PIT answer.
    """
    engine = sa.create_engine(isolated_database_url)
    try:
        import_official(
            engine, tmp_path, adapter=MOPSSiiMonthlyRevenueAdapter(),
            content=SII_PAGE, period=JULY,
        )
        root = archive_root(tmp_path, month=JULY, content=ARCHIVE_B_SII)
        _, manifest = import_archive(
            engine, tmp_path, source="mops_t21sc03_sii", period=JULY, root=root
        )
        with engine.connect() as connection:
            steel = connection.execute(
                sa.text(
                    "SELECT v.note FROM monthly_revenue_versions v "
                    "JOIN security s ON s.id = v.security_id "
                    "WHERE s.security_code = '2025' AND v.revenue_year = 2026 "
                    "AND v.revenue_month = 7"
                )
            ).scalars().all()
            rows = evidence_rows(connection, "2025", JULY, "mops_t21sc03_sii")
        assert len(steel) == 1
        assert "�" not in steel[0]
        assert claimed_types(rows) == ["legacy_capture_bound"]
        assert manifest.reconciliation["notes_legacy_mangled"] == 1
    finally:
        engine.dispose()


def test_a_note_the_issuer_rewrote_is_a_first_captured_version(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """2608 restated every value and its note when it consolidated a subsidiary.

    Legacy holds what it captured on 2026-08-10; the page holds the restated
    figures. That is a real first-captured version, not a decoding defect.
    """
    engine = sa.create_engine(isolated_database_url)
    try:
        import_official(
            engine, tmp_path, adapter=MOPSSiiMonthlyRevenueAdapter(),
            content=SII_PAGE, period=JULY,
        )
        root = archive_root(tmp_path, month=JULY, content=ARCHIVE_B_SII)
        import_archive(
            engine, tmp_path, source="mops_t21sc03_sii", period=JULY, root=root
        )
        with engine.connect() as connection:
            notes = connection.execute(
                sa.text(
                    "SELECT v.note FROM monthly_revenue_versions v "
                    "JOIN security s ON s.id = v.security_id "
                    "WHERE s.security_code = '2608' AND v.revenue_year = 2026 "
                    "AND v.revenue_month = 7 ORDER BY v.id"
                )
            ).scalars().all()
        assert len(notes) == 2
    finally:
        engine.dispose()


def test_a_legacy_row_with_no_official_version_is_reported_not_invented(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """The source dropped issuers that left the market (Step 22-b, audit §4.7).

    The archive still holds them. Writing a version from the archive alone
    would invent coverage the official source does not have.
    """
    engine = sa.create_engine(isolated_database_url)
    try:
        import_official(
            engine, tmp_path, adapter=MOPSSiiMonthlyRevenueAdapter(),
            content=SII_PAGE, period=MARCH,
        )
        missing = ARCHIVE_A.replace(b"\n1102,", b"\n9999,")
        root = archive_root(tmp_path, month=MARCH, content=missing)
        result, manifest = import_archive(
            engine, tmp_path, source="mops_t21sc03_sii", period=MARCH, root=root
        )
        assert manifest.reconciliation["rows_without_an_official_version"] == 1
        assert manifest.reconciliation["skipped_security_codes"] == ["9999"]
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT count(*) FROM security WHERE security_code = '9999'")
            ) == 0
        assert result.business_versions_created == 0
    finally:
        engine.dispose()


def test_ky_issuers_get_no_evidence_and_stay_invisible(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """Legacy never fetched `_1`, so the archive proves nothing about 5871."""
    engine = sa.create_engine(isolated_database_url)
    try:
        import_official(
            engine, tmp_path, adapter=MOPSSiiMonthlyRevenueAdapter(),
            content=SII_KY_PAGE, period=MARCH, page=RevenuePage.FOREIGN,
        )
        import_official(
            engine, tmp_path, adapter=MOPSSiiMonthlyRevenueAdapter(),
            content=SII_PAGE, period=MARCH,
        )
        root = archive_root(tmp_path, month=MARCH, content=ARCHIVE_A)
        import_archive(
            engine, tmp_path, source="mops_t21sc03_sii", period=MARCH, root=root
        )
        later = datetime.now(UTC) + timedelta(minutes=1)
        with engine.connect() as connection:
            rows = evidence_rows(connection, "5871", MARCH, "mops_t21sc03_sii")
            assert [row["evidence_type"] for row in rows] == ["official"]
            assert claimed_types(rows) == []
            assert rows[0]["published_at"] is None
            assert resolved(
                connection, "5871", MARCH, "mops_t21sc03_sii", later
            ) is None
    finally:
        engine.dispose()


def test_rerunning_the_archive_import_adds_no_evidence_revision(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        import_official(
            engine, tmp_path, adapter=MOPSSiiMonthlyRevenueAdapter(),
            content=SII_PAGE, period=MARCH,
        )
        root = archive_root(tmp_path, month=MARCH, content=ARCHIVE_A)
        import_archive(
            engine, tmp_path, source="mops_t21sc03_sii", period=MARCH, root=root
        )
        with engine.connect() as connection:
            before = connection.scalar(
                sa.text("SELECT count(*) FROM publication_evidence")
            )
        repeated, _ = import_archive(
            engine, tmp_path, source="mops_t21sc03_sii", period=MARCH, root=root
        )
        with engine.connect() as connection:
            after = connection.scalar(
                sa.text("SELECT count(*) FROM publication_evidence")
            )
            observations = connection.scalar(
                sa.text("SELECT count(*) FROM publication_evidence_observations")
            )
        assert after == before
        assert repeated.publication_evidence_created == 0
        assert observations > before
    finally:
        engine.dispose()


def test_rerunning_a_first_capture_month_reports_no_new_evidence(
    isolated_database_url: str, tmp_path: Path
) -> None:
    """The 2026M02-onward window has its own versions, and the count knows it.

    A corrected row's evidence hangs on the version the writer returned, not
    on the official one, so reading only the official versions' evidence
    before the loop would report a rerun's deduplicated rows as newly created
    (review of #38) — an idempotency claim (CLAUDE.md §76, §78) that the
    manifest would then contradict exactly in this window.
    """
    engine = sa.create_engine(isolated_database_url)
    try:
        import_official(
            engine, tmp_path, adapter=MOPSOtcMonthlyRevenueAdapter(),
            content=OTC_JUNE_PAGE, period=JUNE,
        )
        root = archive_root(tmp_path, month=JUNE, content=ARCHIVE_B)
        first, _ = import_archive(
            engine, tmp_path, source="mops_t21sc03_otc", period=JUNE, root=root
        )
        assert first.business_versions_created == 1
        assert first.publication_evidence_created > 0
        repeated, _ = import_archive(
            engine, tmp_path, source="mops_t21sc03_otc", period=JUNE, root=root
        )
        assert repeated.business_versions_created == 0
        assert repeated.publication_evidence_created == 0
        assert repeated.publication_evidence_deduplicated == first.evidence_observations
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text(
                    "SELECT count(*) FROM publication_evidence "
                    "WHERE evidence_type = 'legacy_capture_bound'"
                )
            ) == first.evidence_observations
    finally:
        engine.dispose()


def test_the_manifest_records_the_archive_it_read(
    isolated_database_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(isolated_database_url)
    try:
        import_official(
            engine, tmp_path, adapter=MOPSSiiMonthlyRevenueAdapter(),
            content=SII_PAGE, period=MARCH,
        )
        root = archive_root(tmp_path, month=MARCH, content=ARCHIVE_A)
        _, manifest = import_archive(
            engine, tmp_path, source="mops_t21sc03_sii", period=MARCH, root=root
        )
        reconciliation = manifest.reconciliation
        assert reconciliation["artifact_origin"] == "legacy_archive"
        assert reconciliation["evidence_window"] == "recovered_announcement_date"
        assert reconciliation["archive_path"].endswith("2021M03/market.csv")
        assert reconciliation["archive_file_mtime"]
        assert reconciliation["evidence_types_written"] == ["press_report_bound"]
    finally:
        engine.dispose()


def test_the_cli_walks_a_month_range(
    isolated_database_url: str, tmp_path: Path
) -> None:
    from stock_data_center.ingestion.cli import main

    engine = sa.create_engine(isolated_database_url)
    try:
        for period in (MARCH, RevenuePeriod(2021, 4)):
            import_official(
                engine, tmp_path, adapter=MOPSSiiMonthlyRevenueAdapter(),
                content=SII_PAGE, period=period,
            )
        root = archive_root(tmp_path, month=MARCH, content=ARCHIVE_A)
        archive_root(tmp_path, month=RevenuePeriod(2021, 4), content=ARCHIVE_A)
        code = main([
            "--database-url", isolated_database_url,
            "--purpose", "gap_fill",
            "monthly-revenue-archive", "--source", "mops_t21sc03_sii",
            "--period", "2021-03", "--through", "2021-04",
            "--archive-root", str(root),
            "--raw-root", str(tmp_path / "raw"),
        ])
        assert code == 0
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text(
                    "SELECT count(*) FROM publication_evidence "
                    "WHERE evidence_type = 'press_report_bound'"
                )
            ) == 8
    finally:
        engine.dispose()


def test_the_downgrade_refuses_stored_archive_evidence(
    isolated_database_url: str, tmp_path: Path
) -> None:
    from alembic import command
    from conftest import alembic_config, alembic_head
    from sqlalchemy.exc import DBAPIError

    engine = sa.create_engine(isolated_database_url)
    try:
        import_official(
            engine, tmp_path, adapter=MOPSSiiMonthlyRevenueAdapter(),
            content=SII_PAGE, period=MARCH,
        )
        root = archive_root(tmp_path, month=MARCH, content=ARCHIVE_A)
        import_archive(
            engine, tmp_path, source="mops_t21sc03_sii", period=MARCH, root=root
        )
        with pytest.raises(DBAPIError) as blocked:
            command.downgrade(alembic_config(isolated_database_url), PREVIOUS_HEAD)
        assert blocked.value.orig.sqlstate == "P0001"
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT version_num FROM alembic_version")
            ) == alembic_head()
    finally:
        engine.dispose()


def test_the_downgrade_narrows_the_allowlist_and_upgrade_restores_it(
    isolated_database_url: str,
) -> None:
    from alembic import command
    from conftest import alembic_config

    config = alembic_config(isolated_database_url)
    engine = sa.create_engine(isolated_database_url)
    accepted = sa.text(
        "SELECT count(*) FROM dataset_sources "
        "WHERE dataset_code = 'monthly_revenue' "
        "AND 'press_report_bound' = ANY(accepted_evidence_types)"
    )
    try:
        with engine.connect() as connection:
            assert connection.scalar(accepted) == 2
        command.downgrade(config, PREVIOUS_HEAD)
        with engine.connect() as connection:
            assert connection.scalar(accepted) == 0
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(accepted) == 2
    finally:
        engine.dispose()
