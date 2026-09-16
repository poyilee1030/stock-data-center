"""map datasets to the release rule each follows

Revision ID: 4d9f2a6c8b17
Revises: 3c8e5f1b7a46
Create Date: 2026-09-16

Which rule a dataset follows is a declaration per (dataset_code, source), like
its accepted evidence types (ADR-0010), so opting a source in is configuration
rather than code. Each adapter step declares its own; this migration declares
only the daily-price pilot that exists today.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "4d9f2a6c8b17"
down_revision: str | None = "3c8e5f1b7a46"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dataset_release_rules",
        sa.Column("dataset_code", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.SmallInteger(), nullable=False),
        sa.Column("note", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_code", "source"],
            ["dataset_sources.dataset_code", "dataset_sources.source"],
            name=op.f("fk_dataset_release_rules_dataset_code_dataset_sources"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id", "version"],
            ["release_rules.rule_id", "release_rules.version"],
            name=op.f("fk_dataset_release_rules_rule_id_release_rules"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "dataset_code", "source", name=op.f("pk_dataset_release_rules")
        ),
    )


    # daily_price is the only dataset with both an adapter and a rule today.
    # Its catalog and source rows are seeded here rather than left to the
    # importer's own ON CONFLICT DO NOTHING insert, which would otherwise create
    # them with the conservative official-only allowlist and silently ignore the
    # opt-in.
    op.execute(
        sa.text(
            """
            INSERT INTO dataset_catalog (dataset_code, description, schema_version)
            VALUES ('daily_price',
                    'official per-security daily market observations', 'v1')
            ON CONFLICT (dataset_code) DO NOTHING
            """
        )
    )
    for source in ("twse", "tpex"):
        op.execute(
            sa.text(
                """
                INSERT INTO dataset_sources
                    (dataset_code, source, supports_market_pit, supports_system_pit,
                     publication_time_quality, evidence_status,
                     accepted_evidence_types, is_canonical)
                VALUES ('daily_price', :source, true, true, 0, 'verified',
                        ARRAY['official', 'capture_bound',
                              'release_rule']::varchar[], false)
                ON CONFLICT (dataset_code, source) DO UPDATE
                   SET accepted_evidence_types = (
                           SELECT array_agg(DISTINCT t ORDER BY t)
                             FROM unnest(
                                 dataset_sources.accepted_evidence_types
                                 || EXCLUDED.accepted_evidence_types
                             ) AS t
                       )
                """
            ).bindparams(source=source)
        )
        op.execute(
            sa.text(
                """
                INSERT INTO dataset_release_rules
                    (dataset_code, source, rule_id, version, note)
                VALUES ('daily_price', :source, 'exchange_daily_settled', 1,
                        'Trade date D resolves at 03:00 on D+1 (ADR-0020 §3, decision 1).')
                ON CONFLICT (dataset_code, source) DO NOTHING
                """
            ).bindparams(source=source)
        )


def downgrade() -> None:
    # Leaving the widened allowlist would have sources advertising types no
    # rule can produce any more.
    op.execute(
        sa.text(
            """
            UPDATE dataset_sources
               SET accepted_evidence_types = (
                       SELECT array_agg(DISTINCT t ORDER BY t)
                         FROM unnest(accepted_evidence_types) AS t
                        WHERE t NOT IN ('capture_bound', 'release_rule')
                   )
             WHERE dataset_code = 'daily_price'
            """
        )
    )
    op.drop_table("dataset_release_rules")
