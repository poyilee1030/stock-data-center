"""version security market membership

Revision ID: f3a74c12e690
Revises: d81b5c9a3f20
Create Date: 2026-09-12

Cache impact: none. Phase 3 has no cache. This migration corrects historical
security-state semantics before a public API or cache namespace exists.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f3a74c12e690"
down_revision: str | None = "d81b5c9a3f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PREPARE_METADATA_WITH_MARKET = r"""
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


PREPARE_METADATA_WITHOUT_MARKET = r"""
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
            'name', NEW.name,
            'industry', NEW.industry,
            'listed_on', NEW.listed_on,
            'delisted_on', NEW.delisted_on
        )::text, 'UTF8'), 'sha256'), 'hex');
    RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    op.add_column(
        "security_metadata_versions",
        sa.Column("market", sa.String(length=32), nullable=True),
    )
    op.execute(
        "ALTER TABLE security_metadata_versions "
        "DISABLE TRIGGER immutable_security_metadata"
    )
    op.execute(
        """
        UPDATE security_metadata_versions AS metadata
        SET market = identity.market,
            business_content_hash = encode(digest(convert_to(
                jsonb_build_object(
                    'effective_from', metadata.effective_from,
                    'effective_to', metadata.effective_to,
                    'market', identity.market,
                    'name', metadata.name,
                    'industry', metadata.industry,
                    'listed_on', metadata.listed_on,
                    'delisted_on', metadata.delisted_on
                )::text, 'UTF8'), 'sha256'), 'hex')
        FROM security AS identity
        WHERE identity.id = metadata.security_id
        """
    )
    op.execute(
        "ALTER TABLE security_metadata_versions "
        "ENABLE TRIGGER immutable_security_metadata"
    )
    op.alter_column(
        "security_metadata_versions",
        "market",
        existing_type=sa.String(length=32),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_security_metadata_versions_market_nonempty",
        "security_metadata_versions",
        "market <> ''",
    )
    op.execute(PREPARE_METADATA_WITH_MARKET)
    op.drop_constraint("ck_security_market_nonempty", "security", type_="check")
    op.drop_column("security", "market")


def downgrade() -> None:
    op.add_column(
        "security",
        sa.Column("market", sa.String(length=32), nullable=True),
    )
    op.execute(
        "ALTER TABLE security DISABLE TRIGGER immutable_security_identity"
    )
    op.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (security_id) security_id, market
            FROM security_metadata_versions
            ORDER BY security_id, effective_from DESC, id DESC
        )
        UPDATE security AS identity
        SET market = latest.market
        FROM latest
        WHERE latest.security_id = identity.id
        """
    )
    op.execute("UPDATE security SET market = 'UNKNOWN' WHERE market IS NULL")
    op.execute(
        "ALTER TABLE security ENABLE TRIGGER immutable_security_identity"
    )
    op.alter_column(
        "security",
        "market",
        existing_type=sa.String(length=32),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_security_market_nonempty", "security", "market <> ''"
    )

    op.execute(
        "ALTER TABLE security_metadata_versions "
        "DISABLE TRIGGER immutable_security_metadata"
    )
    op.execute(
        """
        UPDATE security_metadata_versions AS metadata
        SET business_content_hash = encode(digest(convert_to(
            jsonb_build_object(
                'effective_from', metadata.effective_from,
                'effective_to', metadata.effective_to,
                'name', metadata.name,
                'industry', metadata.industry,
                'listed_on', metadata.listed_on,
                'delisted_on', metadata.delisted_on
            )::text, 'UTF8'), 'sha256'), 'hex')
        """
    )
    op.execute(
        "ALTER TABLE security_metadata_versions "
        "ENABLE TRIGGER immutable_security_metadata"
    )
    op.execute(PREPARE_METADATA_WITHOUT_MARKET)
    op.drop_constraint(
        "ck_security_metadata_versions_market_nonempty",
        "security_metadata_versions",
        type_="check",
    )
    op.drop_column("security_metadata_versions", "market")
