"""Step 20-c — a POST resource's request is part of its import's provenance.

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

from stock_data_center.db.metadata import import_manifests
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


def _run(engine, tmp_path, adapter):
    fetcher = StaticFetcher()
    importer = InstitutionalMarketSummaryImporter(
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
    return fetcher, manifest


def test_a_post_resource_records_its_full_request_in_the_manifest(
    engine, tmp_path
) -> None:
    fetcher, manifest = _run(engine, tmp_path, PostingAdapter())
    (sent,) = fetcher.resources
    assert manifest["source_scope"]["request"] == sent.request_identity()
    assert (
        SourceResource.from_json_object(manifest["source_scope"]["request"]) == sent
    )


def test_a_plain_get_resource_scope_is_unchanged(engine, tmp_path) -> None:
    _, manifest = _run(engine, tmp_path, TWSEInstitutionalMarketSummaryAdapter())
    assert "request" not in manifest["source_scope"]
