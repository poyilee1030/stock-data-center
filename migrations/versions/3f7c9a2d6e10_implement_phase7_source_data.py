"""implement Phase 7 institutional and financing source data

Revision ID: 3f7c9a2d6e10
Revises: a4c7e2d91b36
Create Date: 2026-09-12

Cache impact: none. No cache exists in Phase 7. This migration does not
backdate ingestion or publication knowledge timestamps. It recomputes only
storage-generated business hashes for the five Phase 7 tables so that logical
key fields are excluded consistently; PIT visibility is unchanged.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "3f7c9a2d6e10"
down_revision: str | None = "a4c7e2d91b36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


DATASETS = (
    (
        "institutional_investor",
        "institutional_investor_versions",
        "institutional_investor_version_observations",
        "institutional_investor_version_id",
    ),
    (
        "foreign_holding",
        "foreign_holding_versions",
        "foreign_holding_version_observations",
        "foreign_holding_version_id",
    ),
    (
        "institutional_market_summary",
        "institutional_market_summary_versions",
        "institutional_market_summary_version_observations",
        "institutional_market_summary_version_id",
    ),
    (
        "margin_trading",
        "margin_trading_versions",
        "margin_trading_version_observations",
        "margin_trading_version_id",
    ),
    (
        "securities_lending",
        "securities_lending_versions",
        "securities_lending_version_observations",
        "securities_lending_version_id",
    ),
)


CHECKS = {
    "institutional_investor_versions": (
        (
            "institutional_value_present",
            "num_nonnulls(foreign_buy, foreign_sell, foreign_net, "
            "foreign_dealer_buy, foreign_dealer_sell, foreign_dealer_net, "
            "trust_buy, trust_sell, trust_net, dealer_self_buy, "
            "dealer_self_sell, dealer_self_net, dealer_hedge_buy, "
            "dealer_hedge_sell, dealer_hedge_net, dealer_net, total_net) > 0",
        ),
        (
            "institutional_gross_nonnegative",
            "(foreign_buy IS NULL OR foreign_buy >= 0) AND "
            "(foreign_sell IS NULL OR foreign_sell >= 0) AND "
            "(foreign_dealer_buy IS NULL OR foreign_dealer_buy >= 0) AND "
            "(foreign_dealer_sell IS NULL OR foreign_dealer_sell >= 0) AND "
            "(trust_buy IS NULL OR trust_buy >= 0) AND "
            "(trust_sell IS NULL OR trust_sell >= 0) AND "
            "(dealer_self_buy IS NULL OR dealer_self_buy >= 0) AND "
            "(dealer_self_sell IS NULL OR dealer_self_sell >= 0) AND "
            "(dealer_hedge_buy IS NULL OR dealer_hedge_buy >= 0) AND "
            "(dealer_hedge_sell IS NULL OR dealer_hedge_sell >= 0)",
        ),
    ),
    "foreign_holding_versions": (
        (
            "foreign_holding_value_present",
            "num_nonnulls(issued_shares, investable_shares, held_shares, "
            "investable_ratio, held_ratio, foreign_legal_limit_ratio, "
            "mainland_legal_limit_ratio) > 0",
        ),
        (
            "foreign_holding_shares_nonnegative",
            "(issued_shares IS NULL OR issued_shares >= 0) AND "
            "(investable_shares IS NULL OR investable_shares >= 0) AND "
            "(held_shares IS NULL OR held_shares >= 0)",
        ),
        (
            "foreign_holding_ratios_percent",
            "(investable_ratio IS NULL OR investable_ratio BETWEEN 0 AND 100) AND "
            "(held_ratio IS NULL OR held_ratio BETWEEN 0 AND 100) AND "
            "(foreign_legal_limit_ratio IS NULL OR foreign_legal_limit_ratio BETWEEN 0 AND 100) AND "
            "(mainland_legal_limit_ratio IS NULL OR mainland_legal_limit_ratio BETWEEN 0 AND 100)",
        ),
        (
            "foreign_holding_change_reason_nonempty",
            "change_reason IS NULL OR change_reason <> ''",
        ),
    ),
    "institutional_market_summary_versions": (
        ("institutional_summary_market_nonempty", "market <> ''"),
        ("institutional_summary_institution_nonempty", "institution <> ''"),
        ("institutional_summary_value_present", "num_nonnulls(buy, sell, net) > 0"),
        (
            "institutional_summary_gross_nonnegative",
            "(buy IS NULL OR buy >= 0) AND (sell IS NULL OR sell >= 0)",
        ),
    ),
    "margin_trading_versions": (
        (
            "margin_trading_value_present",
            "num_nonnulls(margin_buy, margin_sell, margin_cash_repayment, "
            "margin_previous_balance, margin_balance, margin_next_limit, "
            "margin_utilization_ratio, short_buy, short_sell, "
            "short_stock_repayment, short_previous_balance, short_balance, "
            "short_next_limit, short_utilization_ratio, offset_balance) > 0",
        ),
        (
            "margin_trading_quantities_nonnegative",
            "(margin_buy IS NULL OR margin_buy >= 0) AND "
            "(margin_sell IS NULL OR margin_sell >= 0) AND "
            "(margin_cash_repayment IS NULL OR margin_cash_repayment >= 0) AND "
            "(margin_previous_balance IS NULL OR margin_previous_balance >= 0) AND "
            "(margin_balance IS NULL OR margin_balance >= 0) AND "
            "(margin_next_limit IS NULL OR margin_next_limit >= 0) AND "
            "(short_buy IS NULL OR short_buy >= 0) AND "
            "(short_sell IS NULL OR short_sell >= 0) AND "
            "(short_stock_repayment IS NULL OR short_stock_repayment >= 0) AND "
            "(short_previous_balance IS NULL OR short_previous_balance >= 0) AND "
            "(short_balance IS NULL OR short_balance >= 0) AND "
            "(short_next_limit IS NULL OR short_next_limit >= 0) AND "
            "(offset_balance IS NULL OR offset_balance >= 0)",
        ),
        (
            "margin_trading_ratios_percent",
            "(margin_utilization_ratio IS NULL OR margin_utilization_ratio BETWEEN 0 AND 100) AND "
            "(short_utilization_ratio IS NULL OR short_utilization_ratio BETWEEN 0 AND 100)",
        ),
    ),
    "securities_lending_versions": (
        (
            "securities_lending_value_present",
            "num_nonnulls(previous_balance, borrowed, returned, balance, "
            "next_limit, next_available_limit, adjustment) > 0",
        ),
        (
            "securities_lending_quantities_nonnegative",
            "(previous_balance IS NULL OR previous_balance >= 0) AND "
            "(borrowed IS NULL OR borrowed >= 0) AND "
            "(returned IS NULL OR returned >= 0) AND "
            "(balance IS NULL OR balance >= 0) AND "
            "(next_limit IS NULL OR next_limit >= 0) AND "
            "(next_available_limit IS NULL OR next_available_limit >= 0)",
        ),
        ("securities_lending_note_nonempty", "note IS NULL OR note <> ''"),
    ),
}


def upgrade() -> None:
    for _, version_table, link_table, version_column in DATASETS:
        op.create_table(
            link_table,
            sa.Column(version_column, sa.BigInteger(), nullable=False),
            sa.Column("raw_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("ingest_run_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.ForeignKeyConstraint(
                [version_column],
                [f"{version_table}.id"],
                name=op.f(f"fk_{link_table}_{version_column}_{version_table}"),
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["raw_artifact_id", "ingest_run_id"],
                [
                    "raw_artifact_observations.raw_artifact_id",
                    "raw_artifact_observations.ingest_run_id",
                ],
                name=op.f(
                    f"fk_{link_table}_raw_artifact_id_raw_artifact_observations"
                ),
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint(
                version_column,
                "raw_artifact_id",
                "ingest_run_id",
                name=op.f(f"pk_{link_table}"),
            ),
        )

    for table, checks in CHECKS.items():
        for name, condition in checks:
            op.create_check_constraint(op.f(f"ck_{table}_{name}"), table, condition)

    op.execute(
        r"""
        CREATE FUNCTION stockdc_prepare_phase7_observed_version()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE canonical_content jsonb;
        BEGIN
            PERFORM stockdc_assert_lineage(
                NEW.raw_artifact_id, NEW.ingest_run_id, TG_ARGV[0], NEW.source
            );
            NEW.ingested_at := statement_timestamp();
            canonical_content := to_jsonb(NEW) - ARRAY[
                'id', 'business_content_hash', 'ingested_at',
                'raw_artifact_id', 'ingest_run_id', 'source',
                'security_id', 'trade_date', 'market', 'institution'
            ];
            NEW.business_content_hash := encode(digest(
                convert_to(canonical_content::text, 'UTF8'), 'sha256'
            ), 'hex');
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION stockdc_validate_phase7_observation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE version_id bigint; version_source text;
                run_dataset text; run_source text;
        BEGIN
            version_id := (to_jsonb(NEW) ->> TG_ARGV[2])::bigint;
            EXECUTE format('SELECT source FROM %I WHERE id = $1', TG_ARGV[1])
               INTO version_source USING version_id;
            SELECT dataset_code, source INTO run_dataset, run_source
              FROM ingest_runs WHERE id = NEW.ingest_run_id;
            IF version_source IS NULL OR run_dataset IS NULL THEN
                RAISE EXCEPTION 'Phase 7 observation target/run does not exist'
                    USING ERRCODE = '23503';
            END IF;
            IF run_dataset <> TG_ARGV[0] OR run_source <> version_source THEN
                RAISE EXCEPTION 'Phase 7 observation dataset/source mismatch'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION stockdc_validate_phase7_publication_time()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE observed_on date;
        BEGIN
            IF NEW.published_at IS NULL THEN RETURN NEW; END IF;
            IF NEW.institutional_investor_version_id IS NOT NULL THEN
                SELECT trade_date INTO observed_on FROM institutional_investor_versions
                 WHERE id = NEW.institutional_investor_version_id;
            ELSIF NEW.foreign_holding_version_id IS NOT NULL THEN
                SELECT trade_date INTO observed_on FROM foreign_holding_versions
                 WHERE id = NEW.foreign_holding_version_id;
            ELSIF NEW.institutional_market_summary_version_id IS NOT NULL THEN
                SELECT trade_date INTO observed_on FROM institutional_market_summary_versions
                 WHERE id = NEW.institutional_market_summary_version_id;
            ELSIF NEW.margin_trading_version_id IS NOT NULL THEN
                SELECT trade_date INTO observed_on FROM margin_trading_versions
                 WHERE id = NEW.margin_trading_version_id;
            ELSIF NEW.securities_lending_version_id IS NOT NULL THEN
                SELECT trade_date INTO observed_on FROM securities_lending_versions
                 WHERE id = NEW.securities_lending_version_id;
            ELSE
                RETURN NEW;
            END IF;
            IF (NEW.published_at AT TIME ZONE 'Asia/Taipei')::date < observed_on THEN
                RAISE EXCEPTION
                    'publication % precedes observation date % (Asia/Taipei)',
                    NEW.published_at, observed_on USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER validate_phase7_publication_time
        BEFORE INSERT ON publication_evidence FOR EACH ROW
        EXECUTE FUNCTION stockdc_validate_phase7_publication_time();
        """
    )

    for dataset, version_table, link_table, version_column in DATASETS:
        op.execute(f"DROP TRIGGER prepare_{version_table} ON {version_table}")
        op.execute(
            f"""CREATE TRIGGER prepare_{version_table}
            BEFORE INSERT ON {version_table} FOR EACH ROW
            EXECUTE FUNCTION stockdc_prepare_phase7_observed_version('{dataset}');

            CREATE TRIGGER validate_{link_table}
            BEFORE INSERT ON {link_table} FOR EACH ROW
            EXECUTE FUNCTION stockdc_validate_phase7_observation(
                '{dataset}', '{version_table}', '{version_column}'
            );
            CREATE TRIGGER immutable_{link_table}
            BEFORE UPDATE OR DELETE ON {link_table} FOR EACH ROW
            EXECUTE FUNCTION stockdc_reject_mutation();
            CREATE TRIGGER no_truncate_{link_table}
            BEFORE TRUNCATE ON {link_table} FOR EACH STATEMENT
            EXECUTE FUNCTION stockdc_reject_mutation();

            INSERT INTO {link_table} (
                {version_column}, raw_artifact_id, ingest_run_id
            ) SELECT id, raw_artifact_id, ingest_run_id FROM {version_table};"""
        )

    # The old generic function included market/institution in summary hashes.
    # Rehash every Phase 7 table uniformly under the final contract while
    # preserving all trusted ingestion timestamps and lineage.
    for _, version_table, _, _ in DATASETS:
        op.execute(f"ALTER TABLE {version_table} DISABLE TRIGGER immutable_{version_table}")
        op.execute(
            f"""UPDATE {version_table} AS version
            SET business_content_hash = encode(digest(convert_to(
                (to_jsonb(version) - ARRAY[
                    'id', 'business_content_hash', 'ingested_at',
                    'raw_artifact_id', 'ingest_run_id', 'source',
                    'security_id', 'trade_date', 'market', 'institution'
                ])::text, 'UTF8'), 'sha256'), 'hex')"""
        )
        op.execute(f"ALTER TABLE {version_table} ENABLE TRIGGER immutable_{version_table}")


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS validate_phase7_publication_time ON publication_evidence"
    )
    op.execute("DROP FUNCTION IF EXISTS stockdc_validate_phase7_publication_time()")
    for dataset, version_table, link_table, _ in reversed(DATASETS):
        op.execute(f"DROP TRIGGER IF EXISTS no_truncate_{link_table} ON {link_table}")
        op.execute(f"DROP TRIGGER IF EXISTS immutable_{link_table} ON {link_table}")
        op.execute(f"DROP TRIGGER IF EXISTS validate_{link_table} ON {link_table}")
        op.execute(f"DROP TRIGGER prepare_{version_table} ON {version_table}")
        op.execute(
            f"""CREATE TRIGGER prepare_{version_table}
            BEFORE INSERT ON {version_table} FOR EACH ROW
            EXECUTE FUNCTION stockdc_prepare_observed_version('{dataset}')"""
        )
        op.execute(
            f"ALTER TABLE {version_table} DISABLE TRIGGER immutable_{version_table}"
        )
        op.execute(
            f"""UPDATE {version_table} AS version
            SET business_content_hash = encode(digest(convert_to(
                (to_jsonb(version) - ARRAY[
                    'id', 'business_content_hash', 'ingested_at',
                    'raw_artifact_id', 'ingest_run_id', 'source',
                    'security_id', 'market_index_id', 'trade_date'
                ])::text, 'UTF8'), 'sha256'), 'hex')"""
        )
        op.execute(
            f"ALTER TABLE {version_table} ENABLE TRIGGER immutable_{version_table}"
        )
        op.drop_table(link_table)
    op.execute("DROP FUNCTION IF EXISTS stockdc_validate_phase7_observation()")
    op.execute("DROP FUNCTION IF EXISTS stockdc_prepare_phase7_observed_version()")
    for table, checks in reversed(tuple(CHECKS.items())):
        for name, _ in reversed(checks):
            op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
