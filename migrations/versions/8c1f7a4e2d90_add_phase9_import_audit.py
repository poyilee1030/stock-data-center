"""add Phase 9 import audit, checkpoint, and quarantine storage

Revision ID: 8c1f7a4e2d90
Revises: 6b4e8d1f2a73
Create Date: 2026-09-12

Cache impact: none. Phase 9 does not introduce a cache and this migration only
adds operational import audit state. It does not alter historical business,
publication, ingestion, or derivation semantics.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8c1f7a4e2d90"
down_revision: str | None = "6b4e8d1f2a73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_manifests",
        sa.Column("import_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_code", sa.String(64), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("adapter_version", sa.String(64), nullable=False),
        sa.Column("git_commit", sa.String(64), nullable=False),
        sa.Column("source_scope", postgresql.JSONB(), nullable=False),
        sa.Column("configuration_fingerprint", sa.CHAR(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column(
            "started_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("statement_timestamp()"),
            nullable=False,
        ),
        sa.Column("completed_at", postgresql.TIMESTAMP(timezone=True)),
        sa.Column(
            "result_counts",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "reconciliation",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "warnings",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name=op.f("ck_import_manifests_completed_after_started"),
        ),
        sa.CheckConstraint(
            "configuration_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_import_manifests_configuration_fingerprint_lower_hex"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name=op.f("ck_import_manifests_status_value"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_code", "source"],
            ["dataset_sources.dataset_code", "dataset_sources.source"],
            name=op.f("fk_import_manifests_dataset_code_dataset_sources"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("import_id", name=op.f("pk_import_manifests")),
    )
    op.create_table(
        "import_checkpoints",
        sa.Column("import_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_key", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_ingest_run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("last_raw_artifact_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("statement_timestamp()"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_detail", sa.Text()),
        sa.CheckConstraint(
            "attempt_count > 0",
            name=op.f("ck_import_checkpoints_attempt_count_positive"),
        ),
        sa.CheckConstraint(
            "(last_ingest_run_id IS NULL) = (last_raw_artifact_id IS NULL)",
            name=op.f("ck_import_checkpoints_lineage_pair"),
        ),
        sa.CheckConstraint(
            "resource_key <> ''",
            name=op.f("ck_import_checkpoints_resource_key_nonempty"),
        ),
        sa.CheckConstraint(
            "status IN ('captured', 'succeeded', 'quarantined')",
            name=op.f("ck_import_checkpoints_status_value"),
        ),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["import_manifests.import_id"],
            name=op.f("fk_import_checkpoints_import_id_import_manifests"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_raw_artifact_id", "last_ingest_run_id"],
            [
                "raw_artifact_observations.raw_artifact_id",
                "raw_artifact_observations.ingest_run_id",
            ],
            name=op.f(
                "fk_import_checkpoints_last_raw_artifact_id_raw_artifact_observations"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "import_id", "resource_key", name=op.f("pk_import_checkpoints")
        ),
    )
    op.create_table(
        "import_quarantine",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("import_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_key", sa.Text(), nullable=False),
        sa.Column("ingest_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("reason_detail", sa.Text(), nullable=False),
        sa.Column(
            "quarantined_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("statement_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "reason_code <> ''",
            name=op.f("ck_import_quarantine_reason_code_nonempty"),
        ),
        sa.CheckConstraint(
            "reason_detail <> ''",
            name=op.f("ck_import_quarantine_reason_detail_nonempty"),
        ),
        sa.CheckConstraint(
            "resource_key <> ''",
            name=op.f("ck_import_quarantine_resource_key_nonempty"),
        ),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["import_manifests.import_id"],
            name=op.f("fk_import_quarantine_import_id_import_manifests"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["raw_artifact_id", "ingest_run_id"],
            [
                "raw_artifact_observations.raw_artifact_id",
                "raw_artifact_observations.ingest_run_id",
            ],
            name=op.f(
                "fk_import_quarantine_raw_artifact_id_raw_artifact_observations"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_quarantine")),
    )
    op.execute(
        "CREATE TRIGGER immutable_import_quarantine "
        "BEFORE UPDATE OR DELETE ON import_quarantine "
        "FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation(); "
        "CREATE TRIGGER no_truncate_import_quarantine "
        "BEFORE TRUNCATE ON import_quarantine "
        "FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER no_truncate_import_quarantine ON import_quarantine; "
        "DROP TRIGGER immutable_import_quarantine ON import_quarantine"
    )
    op.drop_table("import_quarantine")
    op.drop_table("import_checkpoints")
    op.drop_table("import_manifests")
