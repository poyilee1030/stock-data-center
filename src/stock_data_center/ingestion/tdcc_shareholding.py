"""TDCC shareholding hooks for the shared raw-first import lifecycle (Step 24-a).

One file is one published week for the whole market — about 4,000 securities,
each a sealed immutable aggregate of seventeen rows (Step 6). The week's
publication instant comes from the `tdcc_weekly@1` release rule the source
declares; nothing here invents one.

Two boundaries are worth naming, because both are deliberate:

* **No security is registered from this file.** TDCC reports custody for every
  code it holds, warrants and beneficiary certificates among them, well beyond
  the v1 universe of 上市/上櫃 securities the exchange feeds establish. A
  snapshot is written for a security we already hold and the rest are counted,
  so identity keeps coming from the exchanges (CLAUDE.md §30, §75).
* **A security whose rows do not form a distribution is quarantined, not the
  week.** `2023/20231020.7z` is a download cut off at exactly 1.5 MiB, 562
  securities short, its last security holding two of seventeen levels. The
  2,786 complete securities before the cut are published facts and import as
  such; the partial one quarantines and the manifest reports the week as
  truncated (owner decision, 2026-09-22). No official endpoint can replace
  that week: OpenData serves the latest only and the portal reaches back about
  a year (audit §4.9).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Connection, Engine

from stock_data_center.db.metadata import (
    import_quarantine,
    publication_evidence,
    raw_artifact_observations,
    security,
)
from stock_data_center.evidence import EvidencePolicyService
from stock_data_center.ingestion.adapters.tdcc_shareholding import (
    LegacyTDCCArchiveAdapter,
    TDCCOpenDataAdapter,
    filename_date,
)
from stock_data_center.ingestion.http import SourceFetcher
from stock_data_center.ingestion.lifecycle import (
    BusinessWriteResult,
    RawFirstAdapter,
    RawFirstImporter,
)
from stock_data_center.ingestion.models import (
    EvidenceContext,
    ParsedTDCCShareholding,
    SourceResource,
    TDCCShareholdingRequest,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.market_data import LineageRef
from stock_data_center.provenance import ArtifactOrigin, IngestPurpose
from stock_data_center.tdcc import (
    TDCCLineageRef,
    TDCCPublication,
    TDCCSnapshotWriter,
)

_TDCC_ADAPTERS = (TDCCOpenDataAdapter, LegacyTDCCArchiveAdapter)


class TDCCShareholdingImporter(
    RawFirstImporter[TDCCShareholdingRequest, ParsedTDCCShareholding]
):
    """Import one published week of the whole market's distribution."""

    def __init__(
        self,
        engine: Engine,
        *,
        raw_store: LocalRawArtifactStore | None = None,
        fetcher: SourceFetcher | None = None,
        writer: TDCCSnapshotWriter | None = None,
        policy: EvidencePolicyService | None = None,
    ) -> None:
        super().__init__(engine, raw_store=raw_store, fetcher=fetcher)
        self._writer = writer or TDCCSnapshotWriter()
        self._policy = policy or EvidencePolicyService()

    def run(self, **kwargs):
        """Archived bytes are `legacy_archive`, and prove no first sighting.

        Both are facts about where the bytes came from rather than caller
        choices (CLAUDE.md §75). The purpose matters as much as the origin:
        reading a 2020 file off disk in 2026 is a `gap_fill` however the run
        was declared, and honouring a declared `first_capture` here would
        write today's instant as a capture bound and, being later than
        `tdcc_weekly@1`, suppress the rule as falsified — pushing that week's
        market visibility to 2026 in append-only storage. The archive file
        carries no first-seen evidence of its own: the legacy job downloaded
        whatever OpenData was serving, and its mtime is a download time.
        Same decision as Step 23-c's archive importer, which does not consult
        `context.purpose` either.
        """
        adapter = kwargs.get("adapter")
        if isinstance(adapter, LegacyTDCCArchiveAdapter):
            kwargs["artifact_origin"] = ArtifactOrigin.LEGACY_ARCHIVE
            kwargs["purpose"] = IngestPurpose.GAP_FILL
        return super().run(**kwargs)

    def _source_scope(
        self,
        adapter: RawFirstAdapter[TDCCShareholdingRequest, ParsedTDCCShareholding],
        request: TDCCShareholdingRequest,
        resource: SourceResource,
    ) -> Mapping[str, object]:
        return {
            "snapshot_date": (
                request.snapshot_date.isoformat() if request.snapshot_date else None
            ),
            "resource_key": resource.resource_key,
            "source_uri": resource.source_uri,
        }

    def _source_semantics(
        self,
        adapter: RawFirstAdapter[TDCCShareholdingRequest, ParsedTDCCShareholding],
    ) -> Mapping[str, object]:
        self._require_adapter(adapter)
        archived = isinstance(adapter, LegacyTDCCArchiveAdapter)
        return {
            "distribution_schema": "tdcc-opendata-v1",
            "artifact_origin": (
                ArtifactOrigin.LEGACY_ARCHIVE.value
                if archived
                else ArtifactOrigin.OFFICIAL_FETCH.value
            ),
            "holder_count_unit": "people",
            "share_unit": "share",
            "ownership_percent_unit": "percent of TDCC custody",
            "adjustment_level": (
                "16 差異數調整 (說明4): the difference between the levels' total "
                "and the issuer's issued shares. Stored negative, as its "
                "publisher's portal renders it, with no holder count."
            ),
            "total_level": "17 合計, published",
        }

    def _dataset_description(
        self,
        adapter: RawFirstAdapter[TDCCShareholdingRequest, ParsedTDCCShareholding],
    ) -> str:
        return "weekly per-security shareholding distribution"

    def _capture_dependencies(
        self,
        *,
        adapter: RawFirstAdapter[TDCCShareholdingRequest, ParsedTDCCShareholding],
        request: TDCCShareholdingRequest,
        parsed: ParsedTDCCShareholding,
        import_id: UUID,
        purpose: IngestPurpose,
        artifact_origin: ArtifactOrigin,
    ) -> UUID:
        """Nothing is fetched here; the import id is what the write needs.

        A quarantined security is recorded against the import that read it
        (ADR-0022 §8), and `_write_business` is the only place that knows
        which securities those are.
        """
        return import_id

    def _write_business(
        self,
        connection: Connection,
        *,
        adapter: RawFirstAdapter[TDCCShareholdingRequest, ParsedTDCCShareholding],
        request: TDCCShareholdingRequest,
        parsed: ParsedTDCCShareholding,
        lineage: LineageRef,
        context: EvidenceContext,
        dependencies: object | None = None,
    ) -> BusinessWriteResult:
        self._require_adapter(adapter)
        tdcc_lineage = TDCCLineageRef(lineage.raw_artifact_id, lineage.ingest_run_id)
        artifact = self._artifact_observation(connection, lineage)
        known = self._known_securities(
            connection, [row.security_code for row in parsed.rows]
        )
        outside: list[str] = []
        written: list[tuple[int, bool]] = []
        for row in parsed.rows:
            security_id = known.get(row.security_code)
            if security_id is None:
                outside.append(row.security_code)
                continue
            snapshot = self._writer.write_snapshot(
                connection,
                security_id=security_id,
                source=adapter.source,
                observation=row.observation,
                lineage=tdcc_lineage,
            )
            written.append((snapshot.version_id, snapshot.created))
        created = sum(1 for _, was_created in written if was_created)

        bound = self._policy.bind(
            connection, dataset_code=adapter.dataset_code, source=adapter.source
        )
        plans = bound.plan_many(
            connection,
            period=parsed.snapshot_date,
            purpose=context.purpose,
            captured_at=context.captured_at,
            versions=written,
        )
        resource_key = adapter.resource(request).resource_key
        if isinstance(dependencies, UUID) and parsed.rejected:
            for item in parsed.rejected:
                connection.execute(
                    sa.insert(import_quarantine).values(
                        import_id=dependencies,
                        resource_key=resource_key,
                        ingest_run_id=lineage.ingest_run_id,
                        raw_artifact_id=lineage.raw_artifact_id,
                        reason_code=item.reason_code,
                        reason_detail=item.detail,
                    )
                )

        existing_evidence = self._existing_evidence(
            connection,
            source=adapter.source,
            version_ids={version_id for version_id, _ in written},
        )
        evidence_seen = 0
        unknown = 0
        claimed: set[str] = set()
        evidence_ids: set[int] = set()
        for version_id, _ in written:
            for planned in plans[version_id]:
                evidence_ids.add(
                    self._writer.append_publication_evidence(
                        connection,
                        source=adapter.source,
                        version_id=version_id,
                        observation=TDCCPublication(
                            evidence_kind=planned.evidence_kind,  # type: ignore[arg-type]
                            published_at=planned.published_at,
                            evidence_source=planned.evidence_source,
                            evidence_type=planned.evidence_type,
                            quality_rank=planned.quality_rank,
                        ),
                        lineage=tdcc_lineage,
                    )
                )
                evidence_seen += 1
                unknown += planned.published_at is None
                claimed.add(planned.evidence_type)
        # Evidence is deduplicated on its own hash, so a rerun of the same week
        # lands on the rows already stored. Only the ids this week did not
        # already carry were created here.
        evidence_created = len(evidence_ids - existing_evidence)

        warnings: list[str] = []
        if parsed.truncated:
            warnings.append(
                f"{parsed.snapshot_date.isoformat()}: the source file is "
                "truncated; the securities after the cut are absent from it"
            )
        return BusinessWriteResult(
            business_versions_created=created,
            business_versions_deduplicated=len(written) - created,
            publication_evidence_created=evidence_created,
            publication_evidence_deduplicated=evidence_seen - evidence_created,
            evidence_observations=evidence_seen,
            unknown_publication_observations=unknown,
            normalized_rows=len(parsed.rows),
            coverage_start=parsed.coverage_start,
            coverage_end=parsed.coverage_end,
            reconciliation={
                "requested_snapshot_date": (
                    request.snapshot_date.isoformat()
                    if request.snapshot_date
                    else None
                ),
                "actual_snapshot_date": parsed.snapshot_date.isoformat(),
                "artifact_origin": (
                    ArtifactOrigin.LEGACY_ARCHIVE.value
                    if isinstance(adapter, LegacyTDCCArchiveAdapter)
                    else ArtifactOrigin.OFFICIAL_FETCH.value
                ),
                "container": parsed.container,
                "archive_member": parsed.member_name,
                # Which file answered, read back from the stored artifact
                # rather than from the fetcher. A resume reads the captured
                # artifact and never calls `fetch()`, so live fetcher state
                # would be empty — or, with one fetcher reused across a
                # 376-week walk, the previous week's (CLAUDE.md §75).
                "archive_file": artifact["source_uri"],
                "archive_filename_date": _filename_date(artifact["source_uri"]),
                "archive_fetched_at": artifact["fetched_at"].isoformat(),
                # Only what this run read. A resumed week reads the captured
                # artifact and never calls `fetch`, and one fetcher serves a
                # whole 376-week walk, so the fetcher's state is trusted only
                # when it describes this very resource.
                "archive_file_mtime": _archive_mtime(self._fetcher, resource_key),
                "archive_candidates": list(
                    _archive_candidates(self._fetcher, resource_key)
                ),
                "snapshot_date_format": parsed.date_format,
                "header_variant": parsed.header_variant,
                "source_fields": list(parsed.source_fields),
                "source_rows": parsed.source_rows,
                "complete_securities": len(parsed.rows),
                "securities_written": len(written),
                "securities_outside_the_v1_universe": len(outside),
                "outside_security_codes": sorted(outside),
                "row_quarantined_count": len(parsed.rejected),
                "row_quarantines": [
                    {
                        "security_code": item.security_code,
                        "reason_code": item.reason_code,
                        "detail": item.detail,
                    }
                    for item in parsed.rejected
                ],
                "truncated_payload": parsed.truncated,
                "dropped_adjustment_holder_counts": (
                    parsed.dropped_adjustment_holder_counts
                ),
                # One file is one week. Whether a week is missing between two
                # imported ones is the coverage report's question (Step 24-b).
                "coverage_validation": "not_evaluated",
                "coverage_gaps": None,
                "publication_time": (
                    bound.rule.evidence_source if bound.rule else "unknown"
                ),
                "availability_time_evidence": sorted(claimed),
            },
            warnings=tuple(warnings),
        )

    @staticmethod
    def _require_adapter(adapter: object) -> None:
        if not isinstance(adapter, _TDCC_ADAPTERS):
            raise TypeError(
                "TDCCShareholdingImporter requires a TDCC shareholding adapter"
            )

    @staticmethod
    def _known_securities(
        connection: Connection, codes: list[str]
    ) -> dict[str, int]:
        """Only securities we already hold; nothing is registered from here."""
        if not codes:
            return {}
        rows = connection.execute(
            sa.select(security.c.security_code, security.c.id).where(
                security.c.security_code.in_(sorted(set(codes)))
            )
        ).all()
        return {code: identifier for code, identifier in rows}

    @staticmethod
    def _artifact_observation(
        connection: Connection, lineage: LineageRef
    ) -> sa.RowMapping:
        """What this run's artifact records: the file, and when it was read."""
        return connection.execute(
            sa.select(
                raw_artifact_observations.c.source_uri,
                raw_artifact_observations.c.fetched_at,
            ).where(
                raw_artifact_observations.c.raw_artifact_id
                == lineage.raw_artifact_id,
                raw_artifact_observations.c.ingest_run_id == lineage.ingest_run_id,
            )
        ).mappings().one()

    @staticmethod
    def _existing_evidence(
        connection: Connection, *, source: str, version_ids: set[int]
    ) -> set[int]:
        """Evidence ids these versions already carry, read in one query."""
        if not version_ids:
            return set()
        return set(
            connection.scalars(
                sa.select(publication_evidence.c.id).where(
                    publication_evidence.c.dataset_code == "tdcc_snapshot",
                    publication_evidence.c.source == source,
                    publication_evidence.c.tdcc_snapshot_version_id.in_(
                        sorted(version_ids)
                    ),
                )
            )
        )


def _read_this_resource(fetcher: object, resource_key: str) -> bool:
    """Whether the fetcher's state describes this run's own read."""
    return getattr(fetcher, "last_resource_key", None) == resource_key


def _archive_mtime(fetcher: object, resource_key: str) -> str | None:
    """The file's own mtime, when this run was the one that read it.

    Absent on a resume, which reads the captured artifact instead; the file
    that answered is recorded from the artifact observation either way.
    """
    if not _read_this_resource(fetcher, resource_key):
        return None
    mtime = getattr(fetcher, "last_mtime", None)
    return mtime.isoformat() if mtime is not None else None


def _archive_candidates(fetcher: object, resource_key: str) -> tuple[str, ...]:
    if not _read_this_resource(fetcher, resource_key):
        return ()
    return tuple(getattr(fetcher, "last_candidates", ()) or ())


def _filename_date(source_uri: str) -> str | None:
    """The date the answering file's own name states, for the manifest.

    Recorded rather than trusted: the content date decides, and the adapter
    already refuses a file whose 資料日期 is not the week that was asked for.
    """
    stated = filename_date(Path(source_uri))
    return stated.isoformat() if stated else None
