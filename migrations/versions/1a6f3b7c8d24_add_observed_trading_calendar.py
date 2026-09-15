"""add the observed trading calendar

Revision ID: 1a6f3b7c8d24
Revises: 7c9e2a4b6d81
Create Date: 2026-09-15

The published artifact is one month of actual trading days, so the version is
month-grained and its business content is the day list. A closure is an absence
from that list; correcting one produces a new version, never an update.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "1a6f3b7c8d24"
down_revision: str | None = "7c9e2a4b6d81"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


EVIDENCE_TARGETS = (
    "security_metadata_version_id",
    "daily_price_version_id",
    "monthly_revenue_version_id",
    "financial_filing_version_id",
    "tdcc_snapshot_version_id",
    "institutional_investor_version_id",
    "foreign_holding_version_id",
    "institutional_market_summary_version_id",
    "margin_trading_version_id",
    "securities_lending_version_id",
    "market_index_version_id",
    "market_index_metadata_version_id",
    "corporate_action_version_id",
    "official_valuation_version_id",
    "security_tag_version_id",
    "xbrl_concept_catalog_version_id",
)

NEW_TARGET = "trading_calendar_version_id"


def _exactly_one_target(columns: tuple[str, ...]) -> str:
    return f"num_nonnulls({', '.join(columns)}) = 1"


def _prepare_publication_evidence(include_calendar: bool) -> str:
    targets = EVIDENCE_TARGETS + ((NEW_TARGET,) if include_calendar else ())
    calendar_branch = (
        """
    ELSIF NEW.trading_calendar_version_id IS NOT NULL THEN
        target_dataset := 'trading_calendar';
        SELECT source INTO target_source FROM trading_calendar_versions
         WHERE id = NEW.trading_calendar_version_id;"""
        if include_calendar
        else ""
    )
    prior_array = ",\n                ".join(f"prior.{name}" for name in targets)
    new_array = ",\n                ".join(f"NEW.{name}" for name in targets)
    return f"""
CREATE OR REPLACE FUNCTION stockdc_prepare_publication_evidence() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target_dataset text;
    target_source text;
    prior publication_evidence%ROWTYPE;
BEGIN
    NEW.recorded_at := statement_timestamp();
    IF NEW.security_metadata_version_id IS NOT NULL THEN
        target_dataset := 'security_metadata';
        SELECT source INTO target_source FROM security_metadata_versions WHERE id = NEW.security_metadata_version_id;
    ELSIF NEW.daily_price_version_id IS NOT NULL THEN
        target_dataset := 'daily_price';
        SELECT source INTO target_source FROM daily_price_versions WHERE id = NEW.daily_price_version_id;
    ELSIF NEW.monthly_revenue_version_id IS NOT NULL THEN
        target_dataset := 'monthly_revenue';
        SELECT source INTO target_source FROM monthly_revenue_versions WHERE id = NEW.monthly_revenue_version_id;
    ELSIF NEW.financial_filing_version_id IS NOT NULL THEN
        target_dataset := 'financial_filing';
        SELECT source INTO target_source FROM financial_filing_versions WHERE id = NEW.financial_filing_version_id;
    ELSIF NEW.tdcc_snapshot_version_id IS NOT NULL THEN
        target_dataset := 'tdcc_snapshot';
        SELECT source INTO target_source FROM tdcc_snapshot_versions WHERE id = NEW.tdcc_snapshot_version_id;
    ELSIF NEW.institutional_investor_version_id IS NOT NULL THEN
        target_dataset := 'institutional_investor';
        SELECT source INTO target_source FROM institutional_investor_versions WHERE id = NEW.institutional_investor_version_id;
    ELSIF NEW.foreign_holding_version_id IS NOT NULL THEN
        target_dataset := 'foreign_holding';
        SELECT source INTO target_source FROM foreign_holding_versions WHERE id = NEW.foreign_holding_version_id;
    ELSIF NEW.institutional_market_summary_version_id IS NOT NULL THEN
        target_dataset := 'institutional_market_summary';
        SELECT source INTO target_source FROM institutional_market_summary_versions WHERE id = NEW.institutional_market_summary_version_id;
    ELSIF NEW.margin_trading_version_id IS NOT NULL THEN
        target_dataset := 'margin_trading';
        SELECT source INTO target_source FROM margin_trading_versions WHERE id = NEW.margin_trading_version_id;
    ELSIF NEW.securities_lending_version_id IS NOT NULL THEN
        target_dataset := 'securities_lending';
        SELECT source INTO target_source FROM securities_lending_versions WHERE id = NEW.securities_lending_version_id;
    ELSIF NEW.market_index_version_id IS NOT NULL THEN
        target_dataset := 'market_index';
        SELECT source INTO target_source FROM market_index_versions WHERE id = NEW.market_index_version_id;
    ELSIF NEW.market_index_metadata_version_id IS NOT NULL THEN
        target_dataset := 'market_index_metadata';
        SELECT source INTO target_source FROM market_index_metadata_versions WHERE id = NEW.market_index_metadata_version_id;
    ELSIF NEW.corporate_action_version_id IS NOT NULL THEN
        target_dataset := 'corporate_action';
        SELECT source INTO target_source FROM corporate_action_versions WHERE id = NEW.corporate_action_version_id;
    ELSIF NEW.official_valuation_version_id IS NOT NULL THEN
        target_dataset := 'official_valuation';
        SELECT source INTO target_source FROM official_valuation_versions WHERE id = NEW.official_valuation_version_id;
    ELSIF NEW.security_tag_version_id IS NOT NULL THEN
        target_dataset := 'security_tag';
        SELECT source INTO target_source FROM security_tag_versions WHERE id = NEW.security_tag_version_id;
    ELSIF NEW.xbrl_concept_catalog_version_id IS NOT NULL THEN
        target_dataset := 'xbrl_concept_catalog';
        SELECT source INTO target_source FROM xbrl_concept_catalog_versions WHERE id = NEW.xbrl_concept_catalog_version_id;{calendar_branch}
    ELSE
        RAISE EXCEPTION 'publication evidence requires exactly one target' USING ERRCODE = '23514';
    END IF;
    IF target_source IS NULL THEN
        RAISE EXCEPTION 'publication evidence target does not exist' USING ERRCODE = '23503';
    END IF;
    IF NEW.dataset_code <> target_dataset OR NEW.source <> target_source THEN
        RAISE EXCEPTION 'evidence dataset/source does not match target' USING ERRCODE = '23514';
    END IF;
    PERFORM stockdc_assert_lineage(NEW.raw_artifact_id, NEW.ingest_run_id, NEW.dataset_code, NEW.source);

    IF NEW.supersedes_evidence_id IS NOT NULL THEN
        SELECT * INTO prior FROM publication_evidence WHERE id = NEW.supersedes_evidence_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'superseded evidence does not exist' USING ERRCODE = '23503';
        END IF;
        IF prior.dataset_code <> NEW.dataset_code OR prior.source <> NEW.source
           OR jsonb_build_array(
                {prior_array}
              ) IS DISTINCT FROM jsonb_build_array(
                {new_array}
              ) THEN
            RAISE EXCEPTION 'supersession must stay on the same target and source' USING ERRCODE = '23514';
        END IF;
    END IF;

    NEW.publication_evidence_hash := encode(digest(convert_to(
        (to_jsonb(NEW) - ARRAY['id', 'publication_evidence_hash', 'recorded_at',
            'raw_artifact_id', 'ingest_run_id'])::text, 'UTF8'), 'sha256'), 'hex');
    RETURN NEW;
END;
$$;
"""


PREPARE_CALENDAR = r"""
CREATE FUNCTION stockdc_prepare_trading_calendar() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM stockdc_assert_lineage(
        NEW.raw_artifact_id, NEW.ingest_run_id, 'trading_calendar', NEW.source
    );
    NEW.ingested_at := statement_timestamp();
    NEW.business_content_hash := encode(digest(convert_to(
        (to_jsonb(NEW) - ARRAY['id', 'business_content_hash', 'ingested_at',
            'raw_artifact_id', 'ingest_run_id', 'market', 'source',
            'calendar_month'])::text, 'UTF8'), 'sha256'), 'hex');
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_trading_calendar_versions_prepare
BEFORE INSERT ON trading_calendar_versions
FOR EACH ROW EXECUTE FUNCTION stockdc_prepare_trading_calendar();

CREATE TRIGGER trg_trading_calendar_versions_immutable
BEFORE UPDATE OR DELETE ON trading_calendar_versions
FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();

CREATE TRIGGER trg_trading_calendar_observations_validate
BEFORE INSERT ON trading_calendar_version_observations
FOR EACH ROW EXECUTE FUNCTION stockdc_validate_phase7_observation(
    'trading_calendar', 'trading_calendar_versions', 'calendar_version_id'
);

CREATE TRIGGER trg_trading_calendar_observations_immutable
BEFORE UPDATE OR DELETE ON trading_calendar_version_observations
FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();
"""


def upgrade() -> None:
    op.execute(
        r"""
        CREATE FUNCTION stockdc_dates_sorted_distinct(days date[])
        RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
            SELECT days = (
                SELECT array_agg(DISTINCT day ORDER BY day)
                  FROM unnest(days) AS day
            )
        $$;
        """
    )
    op.create_table(
        "trading_calendar_versions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("calendar_month", sa.Date(), nullable=False),
        sa.Column("trading_days", postgresql.ARRAY(sa.Date()), nullable=False),
        sa.Column("coverage_through", sa.Date(), nullable=False),
        sa.Column("business_content_hash", sa.CHAR(length=64), nullable=True),
        sa.Column("ingested_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("raw_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingest_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["raw_artifact_id", "ingest_run_id"],
            [
                "raw_artifact_observations.raw_artifact_id",
                "raw_artifact_observations.ingest_run_id",
            ],
            name=op.f(
                "fk_trading_calendar_versions_raw_artifact_id_raw_artifact_observations"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trading_calendar_versions")),
        sa.UniqueConstraint(
            "market",
            "source",
            "calendar_month",
            "business_content_hash",
            name="uq_trading_calendar_business_revision",
        ),
    )
    for name, condition in (
        (
            "calendar_month_is_first_day",
            "calendar_month = date_trunc('month', calendar_month)::date",
        ),
        ("month_has_open_day", "cardinality(trading_days) > 0"),
        (
            "trading_days_sorted_distinct",
            "stockdc_dates_sorted_distinct(trading_days)",
        ),
        (
            "trading_days_inside_month",
            "trading_days[1] >= calendar_month "
            "AND trading_days[cardinality(trading_days)] "
            "    < (calendar_month + INTERVAL '1 month')::date",
        ),
        (
            "coverage_through_inside_month",
            "coverage_through >= trading_days[cardinality(trading_days)] "
            "AND coverage_through >= calendar_month "
            "AND coverage_through < (calendar_month + INTERVAL '1 month')::date",
        ),
    ):
        op.create_check_constraint(
            op.f(f"ck_trading_calendar_versions_{name}"),
            "trading_calendar_versions",
            condition,
        )

    op.create_index(
        "ix_trading_calendar_pit",
        "trading_calendar_versions",
        ["market", "source", "calendar_month", "ingested_at"],
    )

    op.create_table(
        "trading_calendar_version_observations",
        sa.Column("calendar_version_id", sa.BigInteger(), nullable=False),
        sa.Column("raw_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingest_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["calendar_version_id"],
            ["trading_calendar_versions.id"],
            name=op.f(
                "fk_trading_calendar_version_observations_calendar_version_id_"
                "trading_calendar_versions"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["raw_artifact_id", "ingest_run_id"],
            [
                "raw_artifact_observations.raw_artifact_id",
                "raw_artifact_observations.ingest_run_id",
            ],
            name=op.f(
                "fk_trading_calendar_version_observations_raw_artifact_id_"
                "raw_artifact_observations"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "calendar_version_id",
            "raw_artifact_id",
            "ingest_run_id",
            name=op.f("pk_trading_calendar_version_observations"),
        ),
    )

    op.add_column(
        "publication_evidence",
        sa.Column(NEW_TARGET, sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        op.f(
            "fk_publication_evidence_trading_calendar_version_id_"
            "trading_calendar_versions"
        ),
        "publication_evidence",
        "trading_calendar_versions",
        [NEW_TARGET],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        op.f("ck_publication_evidence_exactly_one_target"),
        "publication_evidence",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_publication_evidence_exactly_one_target"),
        "publication_evidence",
        _exactly_one_target(EVIDENCE_TARGETS + (NEW_TARGET,)),
    )

    op.execute(PREPARE_CALENDAR)
    op.execute(_prepare_publication_evidence(include_calendar=True))

    op.create_table(
        "dataset_expected_coverage",
        sa.Column("dataset_code", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("cadence", sa.String(length=32), nullable=False),
        sa.Column("period_column", sa.String(length=64), nullable=False),
        sa.Column("window_start", sa.Date(), nullable=False),
        sa.Column("window_end", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_code"],
            ["dataset_catalog.dataset_code"],
            name=op.f("fk_dataset_expected_coverage_dataset_code_dataset_catalog"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "dataset_code", "market", name=op.f("pk_dataset_expected_coverage")
        ),
        sa.CheckConstraint(
            "cadence IN ('trading_day', 'calendar_month')",
            name=op.f("ck_dataset_expected_coverage_cadence_value"),
        ),
        sa.CheckConstraint(
            "window_end IS NULL OR window_end >= window_start",
            name=op.f("ck_dataset_expected_coverage_window_order"),
        ),
    )

    op.execute(
        sa.text(
            """
            INSERT INTO dataset_catalog (dataset_code, description, schema_version)
            VALUES ('trading_calendar',
                    'official market trading calendar of actual open days',
                    'v1')
            ON CONFLICT (dataset_code) DO NOTHING
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO dataset_sources
                (dataset_code, source, supports_market_pit, supports_system_pit,
                 publication_time_quality, evidence_status,
                 accepted_evidence_types, is_canonical)
            VALUES ('trading_calendar', 'twse', false, true, 0, 'unverified',
                    ARRAY['official']::varchar[], true)
            ON CONFLICT (dataset_code, source) DO NOTHING
            """
        )
    )
    # Each adapter PR declares its own expected coverage next to the adapter
    # that fills it; PR #16 declares only the calendar it owns.
    op.execute(
        sa.text(
            """
            INSERT INTO dataset_expected_coverage
                (dataset_code, market, cadence, period_column, window_start, note)
            VALUES ('trading_calendar', 'TWSE', 'calendar_month', 'calendar_month',
                    DATE '2020-01-01',
                    'TWSE FMTQIK publishes one report per calendar month (audit 4.12).')
            ON CONFLICT (dataset_code, market) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # The declarations reference dataset_catalog, so they go before its rows.
    op.drop_table("dataset_expected_coverage")
    op.execute(
        "DELETE FROM dataset_sources WHERE dataset_code = 'trading_calendar'"
    )
    op.execute(
        "DELETE FROM dataset_catalog WHERE dataset_code = 'trading_calendar'"
    )
    op.execute(_prepare_publication_evidence(include_calendar=False))
    op.execute(
        "DROP TRIGGER trg_trading_calendar_observations_immutable "
        "ON trading_calendar_version_observations;"
        "DROP TRIGGER trg_trading_calendar_observations_validate "
        "ON trading_calendar_version_observations;"
        "DROP TRIGGER trg_trading_calendar_versions_immutable "
        "ON trading_calendar_versions;"
        "DROP TRIGGER trg_trading_calendar_versions_prepare "
        "ON trading_calendar_versions;"
        "DROP FUNCTION stockdc_prepare_trading_calendar();"
    )
    op.drop_constraint(
        op.f("ck_publication_evidence_exactly_one_target"),
        "publication_evidence",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_publication_evidence_exactly_one_target"),
        "publication_evidence",
        _exactly_one_target(EVIDENCE_TARGETS),
    )
    op.drop_constraint(
        op.f(
            "fk_publication_evidence_trading_calendar_version_id_"
            "trading_calendar_versions"
        ),
        "publication_evidence",
        type_="foreignkey",
    )
    op.drop_column("publication_evidence", NEW_TARGET)
    op.drop_table("trading_calendar_version_observations")
    op.drop_index("ix_trading_calendar_pit", table_name="trading_calendar_versions")
    op.drop_table("trading_calendar_versions")
    op.execute("DROP FUNCTION stockdc_dates_sorted_distinct(date[]);")
