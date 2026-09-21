"""Financial-filing hooks for the shared raw-first import lifecycle (Step 23-b).

One `t164sb01` document becomes one immutable sealed aggregate: a draft
`financial_filing_versions` row, its statement facts, its curated `basic_eps`
rows, and the seal that makes it PIT-visible. Nothing is visible before the
seal, and after it PostgreSQL rejects every mutation (CLAUDE.md §20).

What is stored is the scope legacy `stock_db` stored — the balance sheet, the
statement of comprehensive income and the statement of cash flows — and the
adapter refuses a financial-industry issuer or a filer outside 上市/上櫃 before
any of it is written.

Publication evidence is whatever the source's policy allows, and in 23-b that
is nothing: `mops_t164sb01` accepts only `official` and follows no release
rule, so every filing records `unknown`. It is System-PIT visible and
Market-PIT invisible until Step 23-c attaches the evidence — late, never
early, exactly as Step 22-a left monthly revenue.

Re-importing a document that is already stored writes no second version and no
second fact: `filing_key` carries the revision fingerprint, so the same
document is the same source revision, and the repeated fetch is recorded as
another observation of it (CLAUDE.md §26). A corrected document fingerprints
differently and becomes its own business version.
"""

from __future__ import annotations

from collections.abc import Mapping

import sqlalchemy as sa
from sqlalchemy import Connection, Engine

from stock_data_center.db.metadata import (
    financial_filing_seals,
    financial_filing_versions,
)
from stock_data_center.evidence import EvidencePolicyService
from stock_data_center.evidence.plan import PlannedEvidence
from stock_data_center.financials.ingestion import (
    FilingLineageRef,
    FinancialFilingObservation,
    FinancialFilingWriter,
    FinancialPublication,
    QuarterlySummaryObservation,
)
from stock_data_center.ingestion.adapters.financial_filing import (
    FILING_REVISION_RULE,
    REPORT_CATEGORIES,
    MOPSFinancialFilingAdapter,
)
from stock_data_center.ingestion.adapters.financial_filing_archive import (
    EVIDENCE_LABEL,
    STATUTORY_RULE,
    LegacyFinancialFilingArchiveAdapter,
)
from stock_data_center.ingestion.http import ArchiveGlobFetcher, SourceFetcher
from stock_data_center.ingestion.lifecycle import (
    BusinessWriteResult,
    RawFirstAdapter,
    RawFirstImporter,
)
from stock_data_center.ingestion.models import (
    EvidenceContext,
    FinancialFilingArchiveRequest,
    ParsedFinancialFiling,
    SourceResource,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.market_data import MarketDataWriter
from stock_data_center.provenance import ArtifactOrigin

BASIC_EPS = "basic_eps"


class FinancialFilingImporter(RawFirstImporter[object, ParsedFinancialFiling]):
    """Import one filer's statement document for one quarter."""

    def __init__(
        self,
        engine: Engine,
        *,
        raw_store: LocalRawArtifactStore | None = None,
        fetcher: SourceFetcher | None = None,
        security_writer: MarketDataWriter | None = None,
        writer: FinancialFilingWriter | None = None,
        policy: EvidencePolicyService | None = None,
    ) -> None:
        super().__init__(engine, raw_store=raw_store, fetcher=fetcher)
        self._security_writer = security_writer or MarketDataWriter()
        self._writer = writer or FinancialFilingWriter()
        self._policy = policy or EvidencePolicyService()

    def _source_scope(
        self,
        adapter: RawFirstAdapter[object, ParsedFinancialFiling],
        request: object,
        resource: SourceResource,
    ) -> Mapping[str, object]:
        return {
            "security_code": request.security_code,  # type: ignore[attr-defined]
            "period": request.period_label,  # type: ignore[attr-defined]
            "report_id": getattr(request, "report_id", "from the document"),
            "resource_key": resource.resource_key,
            "source_uri": resource.source_uri,
        }

    def _source_semantics(self, adapter) -> Mapping[str, object]:
        if not isinstance(adapter, MOPSFinancialFilingAdapter):
            raise TypeError(
                "FinancialFilingImporter requires a MOPSFinancialFilingAdapter"
            )
        return {
            "stored": "balance sheet, comprehensive income and cash flows, "
            "the three statements legacy stock_db stored",
            "not_stored": "權益變動表, the notes, the 附表 and every "
            'escape="true" narrative block',
            "amount_unit": "as printed × 10^scale, in the fact's own unit",
            "encoding": "utf-8 then cp950; MOPS declares big5 and does not mean it",
            "filing_revision_rule": FILING_REVISION_RULE,
            "context_role_rule": "mops-xbrl-context-role:v1",
            "excluded": "financial-industry issuers and filers outside 上市/上櫃",
        }

    def _dataset_description(self, adapter) -> str:
        return "iXBRL financial statement filings"

    def _write_business(
        self,
        connection: Connection,
        *,
        adapter,
        request: object,
        parsed: ParsedFinancialFiling,
        lineage,
        context: EvidenceContext,
        dependencies: object | None = None,
    ) -> BusinessWriteResult:
        if not isinstance(adapter, MOPSFinancialFilingAdapter):
            raise TypeError(
                "FinancialFilingImporter requires a MOPSFinancialFilingAdapter"
            )
        filing_lineage = FilingLineageRef(
            lineage.raw_artifact_id, lineage.ingest_run_id
        )
        security_ids = self._security_writer.register_securities(
            connection, security_codes=[parsed.security_code]
        )
        written = self._writer.begin_filing(
            connection,
            security_id=security_ids[parsed.security_code],
            source=adapter.source,
            observation=FinancialFilingObservation(
                filing_key=parsed.filing_key,
                report_year=parsed.report_year,
                report_quarter=parsed.report_quarter,
                period_start=parsed.period_start,
                period_end=parsed.period_end,
                currency=parsed.currency,
                report_category=REPORT_CATEGORIES[parsed.report_category],
            ),
            lineage=filing_lineage,
        )
        sealed_already = connection.scalar(
            sa.select(financial_filing_seals.c.filing_version_id).where(
                financial_filing_seals.c.filing_version_id == written.version_id
            )
        )
        facts_written = 0
        if written.created or sealed_already is None:
            # A draft left behind by an interrupted run is finished here; a
            # sealed one is this document already stored, and the repeated
            # fetch is recorded as another observation of it, not as facts.
            fact_ids = [
                self._writer.append_fact(
                    connection,
                    version_id=written.version_id,
                    observation=fact.observation,
                )
                for fact in parsed.facts
            ]
            facts_written = len(fact_ids)
            self._append_eps(
                connection,
                parsed=parsed,
                version_id=written.version_id,
                fact_ids=fact_ids,
            )
            self._writer.seal(connection, version_id=written.version_id)

        planned, publication_time = self._plan_evidence(
            connection,
            adapter=adapter,
            parsed=parsed,
            context=context,
            version_created=written.created,
            version_id=written.version_id,
        )
        evidence_ids: set[int] = set()
        unknown = 0
        claimed: set[str] = set()
        for entry in planned:
            evidence_ids.add(
                self._writer.append_publication_evidence(
                    connection,
                    source=adapter.source,
                    version_id=written.version_id,
                    observation=FinancialPublication(
                        evidence_kind=entry.evidence_kind,  # type: ignore[arg-type]
                        published_at=entry.published_at,
                        evidence_source=entry.evidence_source,
                        evidence_type=entry.evidence_type,
                        quality_rank=entry.quality_rank,
                    ),
                    lineage=filing_lineage,
                )
            )
            unknown += entry.published_at is None
            claimed.add(entry.evidence_type)

        return BusinessWriteResult(
            business_versions_created=int(written.created),
            business_versions_deduplicated=int(not written.created),
            publication_evidence_created=len(evidence_ids) if written.created else 0,
            publication_evidence_deduplicated=(
                0 if written.created else len(evidence_ids)
            ),
            evidence_observations=len(planned),
            unknown_publication_observations=unknown,
            normalized_rows=len(parsed.facts),
            coverage_start=parsed.coverage_start,
            coverage_end=parsed.coverage_end,
            reconciliation={
                "security_code": parsed.security_code,
                "period": f"{parsed.report_year:04d}Q{parsed.report_quarter}",
                "report_category": REPORT_CATEGORIES[parsed.report_category],
                "report_id": parsed.report_id,
                "market": parsed.market_code,
                "filing_key": parsed.filing_key,
                "filing_revision_rule": FILING_REVISION_RULE,
                "stored_facts": facts_written or len(parsed.facts),
                "statements": parsed.statement_counts,
                "source_facts_in_document": parsed.source_fact_count,
                "facts_outside_the_statements": (
                    parsed.source_fact_count - len(parsed.facts)
                ),
                "narrative_blocks_not_stored": parsed.note_block_count,
                "eps_bases": [entry.period_basis.value for entry in parsed.eps],
                "parser_version": parsed.parser_version,
                "coverage_validation": "not_evaluated",
                "coverage_gaps": None,
                "publication_time": publication_time,
                "availability_time_evidence": sorted(claimed),
            },
        )

    def _plan_evidence(
        self,
        connection: Connection,
        *,
        adapter,
        parsed: ParsedFinancialFiling,
        context: EvidenceContext,
        version_created: bool,
        version_id: int,
    ) -> tuple[tuple[PlannedEvidence, ...], str]:
        """What this import may claim, and what to call it in the manifest.

        An official fetch claims whatever its declared purpose entitles it to
        (ADR-0020 §5), which for `mops_t164sb01` means a capture bound when it
        is genuinely the first sighting and `unknown` otherwise: the source
        declares no release rule, deliberately.
        """

        bound = self._policy.bind(
            connection, dataset_code=adapter.dataset_code, source=adapter.source
        )
        planned = bound.plan(
            connection,
            period=parsed.period_end,
            purpose=context.purpose,
            version_created=version_created,
            captured_at=context.captured_at,
            version_id=version_id,
        )
        return planned, (
            bound.rule.evidence_source if bound.rule else "unknown"
        )

    def _append_eps(
        self,
        connection: Connection,
        *,
        parsed: ParsedFinancialFiling,
        version_id: int,
        fact_ids: list[int],
    ) -> None:
        """Curate `basic_eps` from the fact the income statement printed.

        Step 5 requires a validated `SourceContextClassification` for every
        `basic_eps`, and refuses to read a basis out of a duration's length.
        The adapter produced one per current role under
        `mops-xbrl-context-role:v1`, each naming the row it read, so the
        summary points at the id that row was written with. Searching for the
        row by its value instead would be ambiguous: a single-quarter EPS can
        equal the year-to-date one (6160 2024Q2 prints -0.41 twice), the two
        share concept, unit, statement and period end, and tying both bases to
        one context makes the seal refuse the whole filing.
        """

        for entry in parsed.eps:
            fact_id = fact_ids[entry.fact_index]
            self._writer.append_summary(
                connection,
                version_id=version_id,
                observation=QuarterlySummaryObservation(
                    metric_code=BASIC_EPS,
                    period_basis=entry.period_basis,
                    value=entry.value,
                    unit_identity=entry.unit_identity,
                    source_fact_id=fact_id,
                    source_context_classification=entry.classification,
                ),
            )


class FinancialFilingArchiveImporter(FinancialFilingImporter):
    """The same import, reading the legacy archive's files instead of MOPS.

    Only the fetcher differs: the bytes come off disk and their artifact origin
    is `legacy_archive`, never `official_fetch` (CLAUDE.md §75). The document,
    the contract and the source are the official adapter's, so an archived copy
    of a filing already fetched officially dedups against it.
    """

    def __init__(
        self,
        engine: Engine,
        *,
        raw_store: LocalRawArtifactStore | None = None,
        fetcher: SourceFetcher | None = None,
        security_writer: MarketDataWriter | None = None,
        writer: FinancialFilingWriter | None = None,
        policy: EvidencePolicyService | None = None,
    ) -> None:
        super().__init__(
            engine,
            raw_store=raw_store,
            fetcher=fetcher or ArchiveGlobFetcher(media_type="text/html"),
            security_writer=security_writer,
            writer=writer,
            policy=policy,
        )

    def run(self, **kwargs: object) -> object:
        """Record `legacy_archive`, whatever the caller passed.

        The bytes came off disk. A caller that forgot the keyword would
        otherwise file a legacy archive copy as an official fetch of
        `mops_t164sb01` (CLAUDE.md §75).
        """

        kwargs["artifact_origin"] = ArtifactOrigin.LEGACY_ARCHIVE
        return super().run(**kwargs)  # type: ignore[arg-type]

    def _plan_evidence(
        self,
        connection: Connection,
        *,
        adapter,
        parsed: ParsedFinancialFiling,
        context: EvidenceContext,
        version_created: bool,
        version_id: int,
    ) -> tuple[tuple[PlannedEvidence, ...], str]:
        """What the archived file proves, which is one of exactly two things.

        Step 23-c. The file's mtime decides (audit §4.8). A daily-job file from
        2025Q4 onward was written when the legacy scraper first saw the filing,
        so it is a `legacy_capture_bound` at that instant — someone else's
        sighting, dated to the second, one rank below our own capture. Every
        other file came from a bulk run that noticed the filing long after it
        was published, so it proves nothing and the filing resolves by
        `financial_statements_general@1` instead.

        The two are exclusive rather than both written: a capture already
        outranks the rule, and where the capture is *later* than the deadline
        the filing was filed late and the rule is falsified for it
        (CLAUDE.md §32). Writing both would leave the falsified instant stored,
        ready to become the answer if the capture were ever superseded.

        `context.purpose` is not consulted. It describes what *this* run did,
        and this run did not see the filing first — the legacy scraper did,
        years ago, and the file is the record of it.
        """

        if not isinstance(adapter, LegacyFinancialFilingArchiveAdapter):
            raise TypeError(
                "FinancialFilingArchiveImporter requires a "
                "LegacyFinancialFilingArchiveAdapter"
            )
        bound_at = adapter.capture_bound(self._request_for(parsed))
        planner = self._policy.bind(
            connection, dataset_code=adapter.dataset_code, source=adapter.source
        ).archive_planner(
            connection,
            period=parsed.period_end,
            rule_id=STATUTORY_RULE[0],
            rule_version=STATUTORY_RULE[1],
            # The rule resolution asks the trading calendar, which refuses
            # outside its imported coverage. A captured file never needs it.
            needs_rule=bound_at is None,
            evidence_label=EVIDENCE_LABEL,
        )
        planned = planner.plan(
            bound_at=bound_at,
            proves_first_capture=bound_at is not None,
            bound_is_the_rule_day=bound_at is None,
        )
        return planned, (
            "legacy archive capture" if bound_at is not None
            else f"{STATUTORY_RULE[0]}@{STATUTORY_RULE[1]}"
        )

    @staticmethod
    def _request_for(
        parsed: ParsedFinancialFiling,
    ) -> FinancialFilingArchiveRequest:
        """The request that found this document, rebuilt from the document.

        The header and the request agree — the parser refuses them otherwise —
        so rebuilding it here keeps the evidence keyed to what was actually
        parsed rather than to what a caller asked for.
        """

        return FinancialFilingArchiveRequest(
            parsed.security_code, parsed.report_year, parsed.report_quarter
        )


def filing_versions_for(
    connection: Connection, *, security_id: int, report_year: int, report_quarter: int
) -> list[int]:
    """Every stored version of one filer's quarter, oldest first."""

    return list(
        connection.scalars(
            sa.select(financial_filing_versions.c.id)
            .where(
                financial_filing_versions.c.security_id == security_id,
                financial_filing_versions.c.report_year == report_year,
                financial_filing_versions.c.report_quarter == report_quarter,
            )
            .order_by(financial_filing_versions.c.id)
        )
    )
