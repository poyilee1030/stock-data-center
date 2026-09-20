"""Import the legacy monthly-revenue archive as evidence (Step 22-c).

The official pages carry no publication instant, so every version Step 22-b
wrote records `unknown` and Market PIT sees nothing. This import is where that
changes, from the one record that survives: the legacy `market.csv`, read as
`legacy_archive` bytes and never as an official response.

Two windows, because the file holds two different things (audit §7.1, §7.4):

* **2020M01–2026M01** — `revswarm` recovered each filing's announcement date
  and wrote it into the file. The date is evidence; the value beside it is
  not, because MOPS serves the latest corrected number and the first-published
  one is not recoverable (ROADMAP Step 22, out of scope). So this window
  attaches evidence to the version we already hold and writes no version of
  its own. Where the source corrected a row after legacy read it, the
  announcement date ends up on the corrected value, which makes that value
  visible from the announcement rather than from the correction: 166 rows
  across the window, an accepted and documented look-ahead (owner decision,
  2026-09-20; audit §4.7).
* **2026M02 onward** — the dates are the legacy 22:45 job's own first-seen
  dates and the values are what it captured then, so the rows import as
  observations. A row the issuer corrected afterwards becomes its own version,
  and the corrected one stays Market-PIT invisible until something captures
  it, which is the point.

What the archive does not hold, it does not prove. Legacy hard-coded the `_0`
URL, so there is no foreign issuer in it at all; KY issuers keep `unknown`
until Step 27's forward capture proves a real one (owner decision). An issuer
the official source no longer lists — 540 of them, Step 22-b — has no version
to carry evidence, and no version is invented for it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy import Connection, Engine

from stock_data_center.db.metadata import monthly_revenue_versions, security
from stock_data_center.evidence import EvidencePolicyService
from stock_data_center.ingestion.adapters.monthly_revenue_archive import (
    LegacyMonthlyRevenueArchiveAdapter,
)
from stock_data_center.ingestion.http import SourceFetcher
from stock_data_center.ingestion.lifecycle import (
    BusinessWriteResult,
    RawFirstAdapter,
    RawFirstImporter,
)
from stock_data_center.ingestion.models import (
    EvidenceContext,
    FetchedArtifact,
    MonthlyRevenueArchiveRequest,
    ParsedMonthlyRevenueArchive,
    SourceDataError,
    SourceResource,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.monthly_revenue.ingestion import (
    MonthlyRevenueObservation,
    MonthlyRevenuePublication,
    MonthlyRevenueWriter,
    RevenueLineageRef,
)
from stock_data_center.monthly_revenue.models import RevenuePeriod
from stock_data_center.provenance import ArtifactOrigin

TAIPEI = ZoneInfo("Asia/Taipei")
END_OF_DAY = time(23, 59, 59)
# The first month whose legacy dates are the 22:45 job's real first sightings.
# Before it, `publish_time` is a recovered announcement date (audit §7.1).
FIRST_CAPTURE_WINDOW_START = RevenuePeriod(2026, 2)
# The statutory filing deadline, registered in Step 15-b. Named here rather
# than declared on the source: declaring it would hand the same instant to
# every row legacy never saw, the KY issuers among them.
STATUTORY_RULE = ("monthly_revenue_statutory", 1)
STATUTORY_DAY = 10
WHITESPACE = re.compile(r"\s+")


class LocalArchiveFetcher:
    """Read one archive file, and record what the file itself says.

    `fetched_at` is the Data Center read time, not a source publication time
    (CLAUDE.md §75). The file's own mtime travels separately, in the manifest.
    """

    def __init__(self) -> None:
        self.last_mtime: datetime | None = None

    def fetch(self, resource: SourceResource) -> FetchedArtifact:
        path = Path(resource.source_uri)
        try:
            content = path.read_bytes()
        except OSError as error:
            raise SourceDataError(
                "archive_unreadable", f"{path}: {error}"
            ) from error
        self.last_mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        return FetchedArtifact(
            content=content,
            source_uri=str(path),
            fetched_at=datetime.now(UTC),
            media_type="text/csv",
        )


def end_of_day(day: date) -> datetime:
    """The end of that day in market time.

    The archive dates a row to the day: for a first sighting the run began at
    22:45 and ended later, and for a news article only the day is known. The
    end of the day is the earliest instant that is certainly not before
    either, and a bound that is slightly late is safe where an early one is
    not (CLAUDE.md §32).
    """
    return datetime.combine(day, END_OF_DAY, tzinfo=TAIPEI)


def statutory_day(period: RevenuePeriod) -> date:
    """The 10th of the month after the revenue month, unshifted.

    Unshifted on purpose: this is the value the legacy file carries when
    `revswarm` recovered nothing, so it is what a row has to equal to be
    treated as rule-bound rather than as a recovered date.
    """
    year, month = (period.year + 1, 1) if period.month == 12 else (
        period.year, period.month + 1
    )
    return date(year, month, STATUTORY_DAY)


def same_published_note(ours: str | None, theirs: str | None) -> bool:
    """Whether two notes are the same published text, read differently.

    Step 22-b measured exactly how legacy's copy differs from the page when
    the issuer did not rewrite anything (audit §4.7): it collapses runs of
    whitespace, it decodes `F9 D7` and friends into U+FFFD, it reads `A1 45`
    as `•` where cp950 gives `‧`, and it stored the literal `NA` as nothing.
    A difference of that shape is our capture defect, not a different filing,
    and forking a version on it would make the mangled text the Market-PIT
    answer.
    """
    if ours == theirs:
        return True
    if theirs is None:
        return ours == "NA"
    if ours is None:
        return False
    ours_text = WHITESPACE.sub(" ", ours)
    theirs_text = WHITESPACE.sub(" ", theirs)
    if ours_text == theirs_text:
        return True
    if "�" in theirs:
        return True
    try:
        return ours_text.encode("cp950") == theirs_text.encode("cp950")
    except UnicodeEncodeError:
        return False


class MonthlyRevenueArchiveImporter(
    RawFirstImporter[MonthlyRevenueArchiveRequest, ParsedMonthlyRevenueArchive]
):
    """Import one market's rows out of one month of the legacy archive."""

    def __init__(
        self,
        engine: Engine,
        *,
        raw_store: LocalRawArtifactStore | None = None,
        fetcher: SourceFetcher | None = None,
        writer: MonthlyRevenueWriter | None = None,
        policy: EvidencePolicyService | None = None,
    ) -> None:
        super().__init__(engine, raw_store=raw_store, fetcher=fetcher or LocalArchiveFetcher())
        self._writer = writer or MonthlyRevenueWriter()
        self._policy = policy or EvidencePolicyService()

    def run(self, **kwargs):
        """Always `legacy_archive`: these bytes are never an official response."""
        kwargs.setdefault("artifact_origin", ArtifactOrigin.LEGACY_ARCHIVE)
        return super().run(**kwargs)

    def _source_scope(
        self,
        adapter: RawFirstAdapter[
            MonthlyRevenueArchiveRequest, ParsedMonthlyRevenueArchive
        ],
        request: MonthlyRevenueArchiveRequest,
        resource: SourceResource,
    ) -> Mapping[str, object]:
        return {
            "period": f"{request.period.year:04d}-{request.period.month:02d}",
            "archive_path": resource.source_uri,
            "resource_key": resource.resource_key,
        }

    def _source_semantics(self, adapter) -> Mapping[str, object]:
        if not isinstance(adapter, LegacyMonthlyRevenueArchiveAdapter):
            raise TypeError(
                "MonthlyRevenueArchiveImporter requires a "
                "LegacyMonthlyRevenueArchiveAdapter"
            )
        return {
            "market": adapter.market,
            "legacy_market": adapter.legacy_market,
            "artifact_origin": ArtifactOrigin.LEGACY_ARCHIVE.value,
            "amount_unit": "TWD, from 千元 × 1,000",
            "encoding": "utf-8 with BOM",
            "publish_time": (
                "recovered announcement date before 2026M02 (audit §7.4); the "
                "legacy 22:45 job's first-seen date from 2026M02 (audit §7.1)"
            ),
        }

    def _dataset_description(self, adapter) -> str:
        return "per-security monthly revenue"

    def _write_business(
        self,
        connection: Connection,
        *,
        adapter,
        request: MonthlyRevenueArchiveRequest,
        parsed: ParsedMonthlyRevenueArchive,
        lineage,
        context: EvidenceContext,
        dependencies: object | None = None,
    ) -> BusinessWriteResult:
        if not isinstance(adapter, LegacyMonthlyRevenueArchiveAdapter):
            raise TypeError(
                "MonthlyRevenueArchiveImporter requires a "
                "LegacyMonthlyRevenueArchiveAdapter"
            )
        period = parsed.period
        first_capture_window = period >= FIRST_CAPTURE_WINDOW_START
        revenue_lineage = RevenueLineageRef(
            lineage.raw_artifact_id, lineage.ingest_run_id
        )
        bound = self._policy.bind(
            connection, dataset_code=adapter.dataset_code, source=adapter.source
        )

        known = self._known_securities(
            connection, [row.security_code for row in parsed.rows]
        )
        held = self._held_versions(
            connection, source=adapter.source, period=period,
            security_ids=set(known.values()),
        )
        rule_day = statutory_day(period)

        created = deduplicated = 0
        skipped: list[str] = []
        mangled_notes = 0
        evidence_created = evidence_seen = 0
        claimed: set[str] = set()
        for row in parsed.rows:
            security_id = known.get(row.security_code)
            official = held.get(security_id) if security_id else None
            if official is None:
                # Either the issuer is unknown to us, or the official source no
                # longer lists it (Step 22-b). Writing a version from the
                # archive alone would invent coverage the source does not have.
                skipped.append(row.security_code)
                continue
            if first_capture_window:
                observation = row.observation
                if same_published_note(official["note"], observation.note) and (
                    official["note"] != observation.note
                ):
                    observation = _with_note(observation, official["note"])
                    mangled_notes += 1
                written = self._writer.append_revenue(
                    connection,
                    security_id=security_id,
                    source=adapter.source,
                    observation=observation,
                    lineage=revenue_lineage,
                )
                version_id = written.version_id
                created += written.created
                deduplicated += not written.created
            else:
                version_id = official["id"]

            for planned in bound.plan_archive(
                connection,
                period=date(period.year, period.month, 1),
                bound_at=end_of_day(row.captured_on),
                evidence_source=(
                    f"legacy market.csv {period.year}M{period.month:02d} "
                    f"{row.captured_on.isoformat()}"
                ),
                proves_first_capture=first_capture_window,
                bound_is_the_rule_day=row.captured_on == rule_day,
                rule_id=STATUTORY_RULE[0],
                rule_version=STATUTORY_RULE[1],
            ):
                before = self._evidence_count(connection, version_id)
                self._writer.append_publication_evidence(
                    connection,
                    source=adapter.source,
                    version_id=version_id,
                    observation=MonthlyRevenuePublication(
                        evidence_kind=planned.evidence_kind,  # type: ignore[arg-type]
                        published_at=planned.published_at,
                        evidence_source=planned.evidence_source,
                        evidence_type=planned.evidence_type,
                        quality_rank=planned.quality_rank,
                    ),
                    lineage=revenue_lineage,
                )
                evidence_seen += 1
                evidence_created += (
                    self._evidence_count(connection, version_id) - before
                )
                claimed.add(planned.evidence_type)

        return BusinessWriteResult(
            business_versions_created=created,
            business_versions_deduplicated=deduplicated,
            publication_evidence_created=evidence_created,
            publication_evidence_deduplicated=evidence_seen - evidence_created,
            evidence_observations=evidence_seen,
            unknown_publication_observations=0,
            normalized_rows=len(parsed.rows),
            coverage_start=parsed.coverage_start,
            coverage_end=parsed.coverage_end,
            reconciliation={
                "requested_period": f"{period.year:04d}-{period.month:02d}",
                "artifact_origin": ArtifactOrigin.LEGACY_ARCHIVE.value,
                "evidence_window": (
                    "legacy_first_capture" if first_capture_window
                    else "recovered_announcement_date"
                ),
                "archive_path": adapter.resource(request).source_uri,
                "archive_file_mtime": _archive_mtime(self._fetcher),
                "header_variant": parsed.header_variant,
                "source_fields": list(parsed.source_fields),
                "archive_rows": parsed.source_rows,
                "market_rows": len(parsed.rows),
                "rows_without_an_official_version": len(skipped),
                "skipped_security_codes": sorted(skipped),
                "notes_legacy_mangled": mangled_notes,
                "evidence_types_written": sorted(claimed),
                "coverage_validation": "not_evaluated",
                "coverage_gaps": None,
                "publication_time": (
                    "legacy capture" if first_capture_window
                    else "recovered announcement date or statutory rule"
                ),
                "availability_time_evidence": sorted(claimed),
            },
        )

    @staticmethod
    def _known_securities(
        connection: Connection, codes: list[str]
    ) -> dict[str, int]:
        """Only securities we already hold. Nothing is registered from here.

        The archive names issuers the official source has since dropped;
        registering them would create identity out of a file legacy wrote.
        """
        if not codes:
            return {}
        rows = connection.execute(
            sa.select(security.c.security_code, security.c.id).where(
                security.c.security_code.in_(sorted(set(codes)))
            )
        ).all()
        return {code: identifier for code, identifier in rows}

    @staticmethod
    def _held_versions(
        connection: Connection,
        *,
        source: str,
        period: RevenuePeriod,
        security_ids: set[int],
    ) -> dict[int, sa.RowMapping]:
        """The earliest version we hold for each security in this month.

        Earliest, because a recovered announcement date describes the filing,
        and the first version we stored is the closest thing we hold to it.
        """
        if not security_ids:
            return {}
        rows = connection.execute(
            sa.select(
                monthly_revenue_versions.c.id,
                monthly_revenue_versions.c.security_id,
                monthly_revenue_versions.c.note,
            )
            .where(
                monthly_revenue_versions.c.source == source,
                monthly_revenue_versions.c.revenue_year == period.year,
                monthly_revenue_versions.c.revenue_month == period.month,
                monthly_revenue_versions.c.security_id.in_(sorted(security_ids)),
            )
            .order_by(
                monthly_revenue_versions.c.security_id,
                monthly_revenue_versions.c.ingested_at,
                monthly_revenue_versions.c.id,
            )
        ).mappings()
        held: dict[int, sa.RowMapping] = {}
        for row in rows:
            held.setdefault(row["security_id"], row)
        return held

    @staticmethod
    def _evidence_count(connection: Connection, version_id: int) -> int:
        from stock_data_center.db.metadata import publication_evidence

        return connection.scalar(
            sa.select(sa.func.count()).select_from(publication_evidence).where(
                publication_evidence.c.monthly_revenue_version_id == version_id
            )
        )


def _with_note(
    observation: MonthlyRevenueObservation, note: str | None
) -> MonthlyRevenueObservation:
    from dataclasses import replace

    return replace(observation, note=note)


def _archive_mtime(fetcher: object) -> str | None:
    mtime = getattr(fetcher, "last_mtime", None)
    return mtime.isoformat() if mtime is not None else None
