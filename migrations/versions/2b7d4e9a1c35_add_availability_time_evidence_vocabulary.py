"""add the availability-time evidence vocabulary

Revision ID: 2b7d4e9a1c35
Revises: 1a6f3b7c8d24
Create Date: 2026-09-15

ADR-0020 §1 and §5. Registers the four availability-time evidence types with the
ranking the ADR fixed, makes that ranking a storage invariant rather than caller
discipline, and records why each ingest fetched and how its bytes were obtained.

Nothing writes the new types yet; Step 15-b derives them from the ingest purpose.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "2b7d4e9a1c35"
down_revision: str | None = "1a6f3b7c8d24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# `rank_is_enforced` is False only for `official`: it predates ADR-0020, existing
# rows and fixtures carry assorted ranks for it, and no v1 source emits it
# affirmatively anyway (audit §7). The four types ADR-0020 introduces are pinned,
# which is what stops a rule from being forged to outrank a capture.
EVIDENCE_TYPES = (
    (
        "official",
        90,
        False,
        "A per-row release instant published by the source itself. No v1 source "
        "provides one (audit §7); the type stays defined and unused for "
        "affirmative evidence.",
    ),
    (
        "capture_bound",
        80,
        True,
        "The Data Center's own first successful fetch instant of an artifact "
        "containing this version. A proven upper bound on publication.",
    ),
    (
        "legacy_capture_bound",
        70,
        True,
        "The legacy scraper's recorded first-seen date, as the end instant of "
        "the scheduled run that first contained the row (audit §7.1). Never the "
        "synthetic deadlines, and never a backfill run.",
    ),
    (
        "press_report_bound",
        60,
        True,
        "A publication date reconstructed from a dated secondary record and "
        "written into the legacy archive, resolved at end of that day, "
        "Asia/Taipei (audit §7.4). Day precision and reconstructed, so it ranks "
        "below both captures; a dated report still proves the value was public, "
        "so it ranks above a rule.",
    ),
    (
        "release_rule",
        40,
        True,
        "A versioned, documented no-later-than instant derived from a published "
        "schedule or statute (ADR-0020 §3). It asserts only that the value must "
        "have been public by then.",
    ),
)


ENFORCE_RANK = r"""
CREATE OR REPLACE FUNCTION stockdc_assert_evidence_rank() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE registered_rank smallint; enforced boolean;
BEGIN
    SELECT quality_rank, rank_is_enforced INTO registered_rank, enforced
      FROM evidence_types WHERE evidence_type = NEW.evidence_type;

    IF registered_rank IS NULL OR NOT enforced THEN
        -- Unregistered types keep ADR-0010's rules: a source's allowlist
        -- decides whether they are accepted at all. Registration is about
        -- ranking, not permission. `official` is registered but unpinned; see
        -- the note on EVIDENCE_TYPES.
        RETURN NEW;
    END IF;

    IF NEW.evidence_kind IN ('assertion', 'correction') THEN
        IF NEW.quality_rank <> registered_rank THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                MESSAGE = 'evidence rank does not match the registered type',
                DETAIL = format(
                    '%s is registered at rank %s, not %s',
                    NEW.evidence_type, registered_rank, NEW.quality_rank
                ),
                HINT = 'ADR-0020 fixes the ranking; change the ADR and this registry, not the row.';
        END IF;
    ELSIF NEW.quality_rank <> 0 THEN
        -- An unknown or retracted head must never outrank a real assertion.
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'non-affirmative evidence must carry rank 0',
            DETAIL = format(
                'evidence_kind %s carries rank %s', NEW.evidence_kind, NEW.quality_rank
            );
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_publication_evidence_rank
BEFORE INSERT ON publication_evidence
FOR EACH ROW EXECUTE FUNCTION stockdc_assert_evidence_rank();
"""


def upgrade() -> None:
    op.create_table(
        "evidence_types",
        sa.Column("evidence_type", sa.String(length=64), nullable=False),
        sa.Column("quality_rank", sa.SmallInteger(), nullable=False),
        sa.Column("rank_is_enforced", sa.Boolean(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("evidence_type", name=op.f("pk_evidence_types")),
        sa.CheckConstraint(
            "quality_rank BETWEEN 0 AND 100",
            name=op.f("ck_evidence_types_quality_rank_range"),
        ),
        sa.CheckConstraint(
            "btrim(description) <> ''",
            name=op.f("ck_evidence_types_description_nonempty"),
        ),
    )
    op.bulk_insert(
        sa.table(
            "evidence_types",
            sa.column("evidence_type", sa.String),
            sa.column("quality_rank", sa.SmallInteger),
            sa.column("rank_is_enforced", sa.Boolean),
            sa.column("description", sa.Text),
        ),
        [
            {
                "evidence_type": name,
                "quality_rank": rank,
                "rank_is_enforced": enforced,
                "description": description,
            }
            for name, rank, enforced, description in EVIDENCE_TYPES
        ],
    )

    op.add_column(
        "ingest_runs",
        sa.Column(
            "purpose",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'unspecified'"),
        ),
    )
    op.create_check_constraint(
        op.f("ck_ingest_runs_purpose_value"),
        "ingest_runs",
        "purpose IN ('first_capture', 'gap_fill', 'correction_check', 'unspecified')",
    )

    op.add_column(
        "raw_artifact_observations",
        sa.Column(
            "artifact_origin",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'official_fetch'"),
        ),
    )
    op.create_check_constraint(
        op.f("ck_raw_artifact_observations_artifact_origin_value"),
        "raw_artifact_observations",
        "artifact_origin IN ('official_fetch', 'legacy_archive')",
    )

    op.execute(ENFORCE_RANK)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER trg_publication_evidence_rank ON publication_evidence;"
        "DROP FUNCTION stockdc_assert_evidence_rank();"
    )
    op.drop_constraint(
        op.f("ck_raw_artifact_observations_artifact_origin_value"),
        "raw_artifact_observations",
        type_="check",
    )
    op.drop_column("raw_artifact_observations", "artifact_origin")
    op.drop_constraint(
        op.f("ck_ingest_runs_purpose_value"), "ingest_runs", type_="check"
    )
    op.drop_column("ingest_runs", "purpose")
    op.drop_table("evidence_types")
