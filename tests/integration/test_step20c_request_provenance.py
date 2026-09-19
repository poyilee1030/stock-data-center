"""Step 20-c — a POST resource's request is part of its import's provenance.

Every capture's `ingest_runs.run_metadata` carries its own request, so a
dependency POST fetched through `_capture_and_parse` is covered as well as the
primary resource (review of #32).

`raw_artifact_observations.source_uri` records a URL. For a GET that is the
whole request; for a POST it is not — every MOPS `t13sa150_otc` date posts
to the same URL and differs only in its form body. CLAUDE.md §71 requires the
request identity in provenance, so the lifecycle records the serialized
request in the manifest's source scope (and therefore its configuration
fingerprint) whenever the URL alone does not identify it. A plain GET adds
nothing, so no existing import's fingerprint changes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from stock_data_center.db.metadata import import_manifests, ingest_runs
from stock_data_center.ingestion.adapters import TWSEInstitutionalMarketSummaryAdapter
from stock_data_center.ingestion.institutional_summary import (
    InstitutionalMarketSummaryImporter,
)
from stock_data_center.ingestion.models import (
    FetchedArtifact,
    InstitutionalMarketSummaryRequest,
    SourceResource,
)
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.provenance import IngestPurpose

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TWSE = (FIXTURES / "twse_bfi82u_20260911.json").read_bytes()
DAY = date(2026, 9, 11)


class StaticFetcher:
    def __init__(self) -> None:
        self.resources: list[SourceResource] = []

    def fetch(self, resource):
        self.resources.append(resource)
        return FetchedArtifact(
            content=TWSE,
            source_uri=resource.source_uri,
            fetched_at=datetime.now(UTC),
            media_type="application/json",
        )


class PostingAdapter(TWSEInstitutionalMarketSummaryAdapter):
    """The Step 20-b adapter, but asking for its date in a form body."""

    def resource(self, request):
        return SourceResource.form_post(
            resource_key=f"{self.source}:post:{request.trade_date.isoformat()}",
            source_uri="https://www.twse.com.tw/rwd/zh/fund/BFI82U",
            fields=(("dayDate", request.trade_date.strftime("%Y%m%d")),
                    ("type", "day"), ("response", "json")),
        )


class DependencyPostingImporter(InstitutionalMarketSummaryImporter):
    """Fetches one auxiliary POST resource beside its primary GET, the way
    the corporate-action importer fetches detail pages."""

    def _capture_dependencies(self, *, adapter, request, parsed, import_id,
                              purpose, artifact_origin):
        self._capture_and_parse(
            PostingAdapter(), request, import_id=import_id,
            purpose=purpose, artifact_origin=artifact_origin,
        )


def _runs(engine, import_id):
    with engine.connect() as connection:
        return {
            row["run_metadata"]["resource_key"]: row["run_metadata"]
            for row in connection.execute(
                sa.select(ingest_runs.c.run_metadata).where(
                    ingest_runs.c.run_metadata["import_id"].astext == str(import_id)
                )
            ).mappings()
        }


def _run(engine, tmp_path, adapter, importer_class=InstitutionalMarketSummaryImporter):
    fetcher = StaticFetcher()
    importer = importer_class(
        engine, raw_store=LocalRawArtifactStore(tmp_path / "raw"), fetcher=fetcher
    )
    import_id = uuid4()
    importer.run(
        adapter=adapter,
        request=InstitutionalMarketSummaryRequest(DAY),
        import_id=import_id,
        git_commit="test-commit",
        purpose=IngestPurpose.GAP_FILL,
    )
    with engine.connect() as connection:
        manifest = connection.execute(
            sa.select(import_manifests).where(import_manifests.c.import_id == import_id)
        ).mappings().one()
    return fetcher, manifest, _runs(engine, import_id)


def test_a_post_resource_records_its_full_request_in_the_manifest(
    engine, tmp_path
) -> None:
    fetcher, manifest, runs = _run(engine, tmp_path, PostingAdapter())
    (sent,) = fetcher.resources
    assert manifest["source_scope"]["request"] == sent.request_identity()
    assert (
        SourceResource.from_json_object(manifest["source_scope"]["request"]) == sent
    )
    assert runs[sent.resource_key]["request"] == sent.request_identity()


def test_a_dependency_post_records_its_request_on_its_own_run(
    engine, tmp_path
) -> None:
    fetcher, _, runs = _run(
        engine, tmp_path, TWSEInstitutionalMarketSummaryAdapter(),
        importer_class=DependencyPostingImporter,
    )
    primary, dependency = fetcher.resources
    assert primary.method == "GET" and dependency.method == "POST"
    assert "request" not in runs[primary.resource_key]
    assert (
        SourceResource.from_json_object(runs[dependency.resource_key]["request"])
        == dependency
    )


def test_a_plain_get_resource_scope_is_unchanged(engine, tmp_path) -> None:
    _, manifest, runs = _run(engine, tmp_path, TWSEInstitutionalMarketSummaryAdapter())
    assert "request" not in manifest["source_scope"]
    assert all("request" not in metadata for metadata in runs.values())
