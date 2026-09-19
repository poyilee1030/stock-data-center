"""monthly revenue: published comparatives and the MOPS sources

Revision ID: d4a7f2c9b8e1
Revises: c6e2a8d4f1b7
Create Date: 2026-09-20

Step 22-a. `monthly_revenue_versions` gains the comparatives MOPS `t21sc03`
publishes in the same row (ROADMAP Step 22, audit §4.7): 上月營收, 去年當月營收,
上月比較增減(%), 去年同月增減(%), 當月累計營收, 去年累計營收, 前期比較增減(%) and
備註. They are stored exactly as published and never reconciled against our
own series (audit §7.3). All are nullable: a blank percentage has no base.

They are business content, so they enter `business_content_hash` — but only
when present. `stockdc_monthly_revenue_hash` hashes `revenue` and `currency`
exactly as before and appends the comparatives with NULLs stripped, so every
version written without them keeps its identity byte for byte; nothing stored
is rewritten.

Two sources, one per market, each its own history (CLAUDE.md §30):
`mops_t21sc03_sii` and `mops_t21sc03_otc`. They accept only `official`
evidence and follow no release rule yet: the publication evidence — recovered
announcement dates, legacy first-seen captures, the statutory rule — is Step
22-c. Until then their versions are System-PIT visible only, which is late and
never early.

Downgrade refuses, before changing anything, once a comparative is stored or
either source has an ingest run, a manifest or a version: dropping either
would discard history (CLAUDE.md §81).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4a7f2c9b8e1"
down_revision: str | None = "c6e2a8d4f1b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "monthly_revenue_versions"
AMOUNTS = (
    "revenue_last_month",
    "revenue_last_year_month",
    "cumulative_revenue",
    "cumulative_revenue_last_year",
)
PERCENTS = ("mom_pct", "yoy_pct", "cumulative_yoy_pct")
# The order the hash function takes them in.
COMPARATIVES = (
    "revenue_last_month", "revenue_last_year_month", "mom_pct", "yoy_pct",
    "cumulative_revenue", "cumulative_revenue_last_year", "cumulative_yoy_pct", "note",
)
SOURCES = {
    "mops_t21sc03_sii": "MOPS nas/t21/sii/t21sc03_<ROC year>_<month>_<0|1>.html (audit 4.7).",
    "mops_t21sc03_otc": "MOPS nas/t21/otc/t21sc03_<ROC year>_<month>_<0|1>.html (audit 4.7).",
}

HASH_FUNCTION = r"""
CREATE FUNCTION stockdc_monthly_revenue_hash(
    revenue numeric, currency text,
    revenue_last_month numeric, revenue_last_year_month numeric,
    mom_pct numeric, yoy_pct numeric,
    cumulative_revenue numeric, cumulative_revenue_last_year numeric,
    cumulative_yoy_pct numeric, note text
) RETURNS char(64)
LANGUAGE sql IMMUTABLE AS $$
    SELECT encode(digest(convert_to((
        jsonb_build_object('revenue', revenue, 'currency', currency)
        || jsonb_strip_nulls(jsonb_build_object(
            'revenue_last_month', revenue_last_month,
            'revenue_last_year_month', revenue_last_year_month,
            'mom_pct', mom_pct,
            'yoy_pct', yoy_pct,
            'cumulative_revenue', cumulative_revenue,
            'cumulative_revenue_last_year', cumulative_revenue_last_year,
            'cumulative_yoy_pct', cumulative_yoy_pct,
            'note', note
        ))
    )::text, 'UTF8'), 'sha256'), 'hex')
$$;
"""

PREPARE_WITH_COMPARATIVES = r"""
CREATE OR REPLACE FUNCTION stockdc_prepare_monthly_revenue() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM stockdc_assert_lineage(
        NEW.raw_artifact_id, NEW.ingest_run_id, 'monthly_revenue', NEW.source
    );
    NEW.ingested_at := statement_timestamp();
    NEW.business_content_hash := stockdc_monthly_revenue_hash(
        NEW.revenue, NEW.currency,
        NEW.revenue_last_month, NEW.revenue_last_year_month,
        NEW.mom_pct, NEW.yoy_pct,
        NEW.cumulative_revenue, NEW.cumulative_revenue_last_year,
        NEW.cumulative_yoy_pct, NEW.note
    );
    RETURN NEW;
END;
$$;
"""

PREPARE_WITHOUT_COMPARATIVES = r"""
CREATE OR REPLACE FUNCTION stockdc_prepare_monthly_revenue() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM stockdc_assert_lineage(
        NEW.raw_artifact_id, NEW.ingest_run_id, 'monthly_revenue', NEW.source
    );
    NEW.ingested_at := statement_timestamp();
    NEW.business_content_hash := encode(digest(convert_to(
        jsonb_build_object(
            'revenue', NEW.revenue,
            'currency', NEW.currency
        )::text, 'UTF8'), 'sha256'), 'hex');
    RETURN NEW;
END;
$$;
"""

GUARD = """
DO $$
DECLARE blocking bigint;
BEGIN
    SELECT (SELECT count(*) FROM monthly_revenue_versions
             WHERE num_nonnulls(revenue_last_month, revenue_last_year_month, mom_pct,
                                yoy_pct, cumulative_revenue, cumulative_revenue_last_year,
                                cumulative_yoy_pct, note) > 0
                OR source IN ('mops_t21sc03_sii', 'mops_t21sc03_otc'))
         + (SELECT count(*) FROM ingest_runs
             WHERE dataset_code = 'monthly_revenue'
               AND source IN ('mops_t21sc03_sii', 'mops_t21sc03_otc'))
         + (SELECT count(*) FROM import_manifests
             WHERE dataset_code = 'monthly_revenue'
               AND source IN ('mops_t21sc03_sii', 'mops_t21sc03_otc'))
      INTO blocking;
    IF blocking > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'cannot downgrade away stored monthly-revenue comparatives or sources',
            DETAIL = format('%s versions, ingest runs or manifests depend on them', blocking),
            HINT = 'history is append-only; remove the history deliberately first';
    END IF;
END $$;
"""


def upgrade() -> None:
    for name in AMOUNTS:
        op.add_column(TABLE, sa.Column(name, sa.Numeric(24, 4), nullable=True))
    for name in PERCENTS:
        op.add_column(TABLE, sa.Column(name, sa.Numeric(24, 4), nullable=True))
    op.add_column(TABLE, sa.Column("note", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_monthly_revenue_versions_note_nonempty", TABLE, "note IS NULL OR note <> ''"
    )
    op.execute(HASH_FUNCTION)
    op.execute(PREPARE_WITH_COMPARATIVES)
    op.execute(
        sa.text(
            """
            INSERT INTO dataset_catalog (dataset_code, description, schema_version)
            VALUES ('monthly_revenue', 'per-security monthly revenue', 'v1')
            ON CONFLICT (dataset_code) DO NOTHING
            """
        )
    )
    for source in SOURCES:
        op.execute(
            sa.text(
                """
                INSERT INTO dataset_sources
                    (dataset_code, source, supports_market_pit, supports_system_pit,
                     publication_time_quality, evidence_status,
                     accepted_evidence_types, is_canonical)
                VALUES ('monthly_revenue', :source, true, true, 0, 'verified',
                        ARRAY['official']::varchar[], false)
                ON CONFLICT (dataset_code, source) DO NOTHING
                """
            ).bindparams(source=source)
        )


def downgrade() -> None:
    op.execute(sa.text(GUARD))
    op.execute(
        sa.text(
            "DELETE FROM dataset_sources WHERE dataset_code = 'monthly_revenue' "
            "AND source IN :sources"
        ).bindparams(sa.bindparam("sources", value=tuple(SOURCES), expanding=True))
    )
    op.execute(
        sa.text(
            """
            DELETE FROM dataset_catalog
             WHERE dataset_code = 'monthly_revenue'
               AND NOT EXISTS (
                     SELECT 1 FROM dataset_sources WHERE dataset_code = 'monthly_revenue'
                   )
            """
        )
    )
    op.execute(PREPARE_WITHOUT_COMPARATIVES)
    op.execute("DROP FUNCTION stockdc_monthly_revenue_hash")
    op.drop_constraint("ck_monthly_revenue_versions_note_nonempty", TABLE, type_="check")
    for name in reversed(COMPARATIVES):
        op.drop_column(TABLE, name)
