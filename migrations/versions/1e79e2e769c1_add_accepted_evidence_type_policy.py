"""add accepted evidence type policy

Revision ID: 1e79e2e769c1
Revises: ae58b8fa158d
Create Date: 2026-09-11 17:07:00.387948
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "1e79e2e769c1"
down_revision: str | None = "ae58b8fa158d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add an exact source-policy allowlist for Market PIT evidence.

    Existing source rows explicitly migrate to the conservative official-only
    policy. Cache impact: none; Phase 2 has no cache implementation or keys.
    """
    op.add_column(
        "dataset_sources",
        sa.Column(
            "accepted_evidence_types",
            postgresql.ARRAY(sa.String(length=64)),
            server_default=sa.text("ARRAY['official']::varchar[]"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_dataset_sources_accepted_evidence_types_valid"),
        "dataset_sources",
        "cardinality(accepted_evidence_types) > 0 "
        "AND array_position(accepted_evidence_types, NULL) IS NULL "
        "AND array_position(accepted_evidence_types, '') IS NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_dataset_sources_accepted_evidence_types_valid"),
        "dataset_sources",
        type_="check",
    )
    op.drop_column("dataset_sources", "accepted_evidence_types")
