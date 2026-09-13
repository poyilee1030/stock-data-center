"""track repeated security metadata transitions

Revision ID: 9a7d3e5c1b20
Revises: 8c1f7a4e2d90
Create Date: 2026-09-13

Cache impact: none. Phase 9 has no cache. This migration preserves System-PIT
ordering when a source reasserts earlier business content after an intervening
same-effective-date correction.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9a7d3e5c1b20"
down_revision: str | None = "8c1f7a4e2d90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PREPARE_METADATA_WITH_PREDECESSOR = r"""
CREATE OR REPLACE FUNCTION stockdc_prepare_security_metadata() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    predecessor_security_id bigint;
    predecessor_source text;
    predecessor_effective_from date;
BEGIN
    PERFORM stockdc_assert_lineage(
        NEW.raw_artifact_id, NEW.ingest_run_id, 'security_metadata', NEW.source
    );

    IF NEW.predecessor_version_id IS NOT NULL THEN
        SELECT security_id, source, effective_from
          INTO predecessor_security_id, predecessor_source,
               predecessor_effective_from
          FROM security_metadata_versions
         WHERE id = NEW.predecessor_version_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'security metadata predecessor % does not exist',
                NEW.predecessor_version_id USING ERRCODE = '23503';
        END IF;
        IF predecessor_security_id IS DISTINCT FROM NEW.security_id
           OR predecessor_source IS DISTINCT FROM NEW.source
           OR predecessor_effective_from > NEW.effective_from THEN
            RAISE EXCEPTION
                'security metadata predecessor % has incompatible identity or effective time',
                NEW.predecessor_version_id USING ERRCODE = '23514';
        END IF;
    END IF;

    NEW.ingested_at := statement_timestamp();
    NEW.business_content_hash := encode(digest(convert_to(
        jsonb_build_object(
            'effective_from', NEW.effective_from,
            'effective_to', NEW.effective_to,
            'market', NEW.market,
            'name', NEW.name,
            'industry', NEW.industry,
            'listed_on', NEW.listed_on,
            'delisted_on', NEW.delisted_on
        )::text, 'UTF8'), 'sha256'), 'hex');
    RETURN NEW;
END;
$$;
"""


PREPARE_METADATA_WITHOUT_PREDECESSOR = r"""
CREATE OR REPLACE FUNCTION stockdc_prepare_security_metadata() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM stockdc_assert_lineage(
        NEW.raw_artifact_id, NEW.ingest_run_id, 'security_metadata', NEW.source
    );
    NEW.ingested_at := statement_timestamp();
    NEW.business_content_hash := encode(digest(convert_to(
        jsonb_build_object(
            'effective_from', NEW.effective_from,
            'effective_to', NEW.effective_to,
            'market', NEW.market,
            'name', NEW.name,
            'industry', NEW.industry,
            'listed_on', NEW.listed_on,
            'delisted_on', NEW.delisted_on
        )::text, 'UTF8'), 'sha256'), 'hex');
    RETURN NEW;
END;
$$;
"""


ASSERT_DOWNGRADE_REPRESENTABLE = r"""
DO $$
DECLARE
    incompatible_group_count bigint;
BEGIN
    SELECT count(*)
      INTO incompatible_group_count
      FROM (
          SELECT security_id, source, effective_from, business_content_hash
            FROM security_metadata_versions
           GROUP BY security_id, source, effective_from, business_content_hash
          HAVING count(*) > 1
      ) AS reassertions;

    IF incompatible_group_count > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'cannot downgrade security metadata transition history',
            DETAIL = format(
                '%s same-date content reassertion group(s) cannot be represented by revision 8c1f7a4e2d90',
                incompatible_group_count
            ),
            HINT = 'Keep revision 9a7d3e5c1b20 or newer; do not delete or collapse append-only PIT history to force this downgrade.';
    END IF;
END;
$$;
"""


def upgrade() -> None:
    op.add_column(
        "security_metadata_versions",
        sa.Column("predecessor_version_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_security_metadata_predecessor",
        "security_metadata_versions",
        "security_metadata_versions",
        ["predecessor_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "uq_security_metadata_business_revision",
        "security_metadata_versions",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_security_metadata_business_revision",
        "security_metadata_versions",
        [
            "security_id",
            "source",
            "effective_from",
            "business_content_hash",
            "predecessor_version_id",
        ],
        postgresql_nulls_not_distinct=True,
    )
    op.execute(PREPARE_METADATA_WITH_PREDECESSOR)


def downgrade() -> None:
    op.execute(ASSERT_DOWNGRADE_REPRESENTABLE)
    op.drop_constraint(
        "uq_security_metadata_business_revision",
        "security_metadata_versions",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_security_metadata_business_revision",
        "security_metadata_versions",
        ["security_id", "source", "effective_from", "business_content_hash"],
    )
    op.drop_constraint(
        "fk_security_metadata_predecessor",
        "security_metadata_versions",
        type_="foreignkey",
    )
    op.drop_column("security_metadata_versions", "predecessor_version_id")
    op.execute(PREPARE_METADATA_WITHOUT_PREDECESSOR)
