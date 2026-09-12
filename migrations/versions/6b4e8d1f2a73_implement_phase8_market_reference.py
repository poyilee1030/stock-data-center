"""implement Phase 8 market reference data

Revision ID: 6b4e8d1f2a73
Revises: 3f7c9a2d6e10
Create Date: 2026-09-12

Cache impact: none. No cache exists. Business hashes are recomputed under the
final Phase 8 contract without changing ingestion or publication timestamps.
"""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "6b4e8d1f2a73"
down_revision: str | None = "3f7c9a2d6e10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DATASETS = (
    ("market_index", "market_index_versions", "market_index_version_observations", "market_index_version_id"),
    ("corporate_action", "corporate_action_versions", "corporate_action_version_observations", "corporate_action_version_id"),
    ("official_valuation", "official_valuation_versions", "official_valuation_version_observations", "official_valuation_version_id"),
)

CHECKS = {
    "market_index": (("market_index_code_nonempty", "index_code <> ''"),
                     ("market_index_market_nonempty", "market <> ''"),
                     ("market_index_name_nonempty", "name <> ''")),
    "market_index_versions": (
        ("market_index_open_nonnegative", "open_value IS NULL OR open_value >= 0"),
        ("market_index_high_nonnegative", "high_value IS NULL OR high_value >= 0"),
        ("market_index_low_nonnegative", "low_value IS NULL OR low_value >= 0"),
        ("market_index_close_nonnegative", "close_value >= 0"),
        ("market_index_trade_value_nonnegative", "trade_value IS NULL OR trade_value >= 0"),
        ("market_index_high_consistent", "high_value IS NULL OR (high_value >= close_value AND (open_value IS NULL OR high_value >= open_value) AND (low_value IS NULL OR high_value >= low_value))"),
        ("market_index_low_consistent", "low_value IS NULL OR (low_value <= close_value AND (open_value IS NULL OR low_value <= open_value))"),
    ),
    "corporate_action_versions": (
        ("corporate_action_announcement_by_ex_date", "announcement_date IS NULL OR announcement_date <= ex_date"),
        ("corporate_action_value_present", "num_nonnulls(announcement_date, record_date, payment_date, cash_dividend_per_share, stock_dividend_ratio, rights_ratio, subscription_price, close_before, reference_price, rights_dividend_value) > 0 OR terms <> '{}'::jsonb"),
        ("corporate_action_values_nonnegative", "(cash_dividend_per_share IS NULL OR cash_dividend_per_share >= 0) AND (stock_dividend_ratio IS NULL OR stock_dividend_ratio >= 0) AND (rights_ratio IS NULL OR rights_ratio >= 0) AND (subscription_price IS NULL OR subscription_price >= 0) AND (close_before IS NULL OR close_before >= 0) AND (reference_price IS NULL OR reference_price >= 0) AND (rights_dividend_value IS NULL OR rights_dividend_value >= 0)"),
    ),
    "official_valuation_versions": (
        ("official_valuation_value_present", "num_nonnulls(pe_ratio, pb_ratio, dividend_yield, dividend_per_share) > 0"),
        ("official_valuation_values_valid", "(pe_ratio IS NULL OR pe_ratio > 0) AND (pb_ratio IS NULL OR pb_ratio > 0) AND (dividend_yield IS NULL OR dividend_yield >= 0) AND (dividend_per_share IS NULL OR dividend_per_share >= 0)"),
        ("official_valuation_dividend_year_valid", "dividend_year IS NULL OR dividend_year BETWEEN 1900 AND 9999"),
        ("official_valuation_report_period_nonempty", "report_period IS NULL OR report_period <> ''"),
    ),
}


def upgrade() -> None:
    for _, versions, links, target in DATASETS:
        op.create_table(links,
            sa.Column(target, sa.BigInteger(), nullable=False),
            sa.Column("raw_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("ingest_run_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.ForeignKeyConstraint([target], [f"{versions}.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["raw_artifact_id", "ingest_run_id"],
                ["raw_artifact_observations.raw_artifact_id", "raw_artifact_observations.ingest_run_id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint(target, "raw_artifact_id", "ingest_run_id"))
    for table, checks in CHECKS.items():
        for name, condition in checks:
            op.create_check_constraint(op.f(f"ck_{table}_{name}"), table, condition)
    op.execute(r"""
        CREATE FUNCTION stockdc_prepare_phase8_observed_version()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE content jsonb;
        BEGIN
          PERFORM stockdc_assert_lineage(NEW.raw_artifact_id, NEW.ingest_run_id, TG_ARGV[0], NEW.source);
          NEW.ingested_at := statement_timestamp();
          content := to_jsonb(NEW) - ARRAY['id','business_content_hash','ingested_at','raw_artifact_id','ingest_run_id','source','security_id','market_index_id','trade_date','action_type','ex_date'];
          NEW.business_content_hash := encode(digest(convert_to(content::text,'UTF8'),'sha256'),'hex');
          RETURN NEW;
        END; $$;
        CREATE FUNCTION stockdc_prepare_market_index_identity()
        RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.created_at := statement_timestamp(); RETURN NEW; END; $$;
        CREATE TRIGGER prepare_market_index_identity BEFORE INSERT ON market_index
        FOR EACH ROW EXECUTE FUNCTION stockdc_prepare_market_index_identity();
        CREATE TRIGGER immutable_market_index BEFORE UPDATE OR DELETE ON market_index
        FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();
        CREATE TRIGGER no_truncate_market_index BEFORE TRUNCATE ON market_index
        FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();

        CREATE FUNCTION stockdc_validate_phase8_observation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE vid bigint; version_source text; run_dataset text; run_source text;
        BEGIN
          vid := (to_jsonb(NEW)->>TG_ARGV[2])::bigint;
          EXECUTE format('SELECT source FROM %I WHERE id=$1', TG_ARGV[1]) INTO version_source USING vid;
          SELECT dataset_code, source INTO run_dataset, run_source FROM ingest_runs WHERE id=NEW.ingest_run_id;
          IF version_source IS NULL OR run_dataset IS NULL THEN RAISE EXCEPTION 'Phase 8 observation target/run missing' USING ERRCODE='23503'; END IF;
          IF run_dataset <> TG_ARGV[0] OR run_source <> version_source THEN RAISE EXCEPTION 'Phase 8 observation dataset/source mismatch' USING ERRCODE='23514'; END IF;
          RETURN NEW;
        END; $$;

        CREATE FUNCTION stockdc_validate_phase8_publication_time()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE lower_date date;
        BEGIN
          IF NEW.published_at IS NULL THEN RETURN NEW; END IF;
          IF NEW.market_index_version_id IS NOT NULL THEN SELECT trade_date INTO lower_date FROM market_index_versions WHERE id=NEW.market_index_version_id;
          ELSIF NEW.official_valuation_version_id IS NOT NULL THEN SELECT trade_date INTO lower_date FROM official_valuation_versions WHERE id=NEW.official_valuation_version_id;
          ELSIF NEW.corporate_action_version_id IS NOT NULL THEN SELECT announcement_date INTO lower_date FROM corporate_action_versions WHERE id=NEW.corporate_action_version_id;
          ELSE RETURN NEW; END IF;
          IF lower_date IS NOT NULL AND (NEW.published_at AT TIME ZONE 'Asia/Taipei')::date < lower_date THEN
            RAISE EXCEPTION 'publication % precedes source date %', NEW.published_at, lower_date USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END; $$;
        CREATE TRIGGER validate_phase8_publication_time BEFORE INSERT ON publication_evidence
        FOR EACH ROW EXECUTE FUNCTION stockdc_validate_phase8_publication_time();
    """)
    for dataset, versions, links, target in DATASETS:
        op.execute(f"DROP TRIGGER prepare_{versions} ON {versions}")
        op.execute(f"""CREATE TRIGGER prepare_{versions} BEFORE INSERT ON {versions}
          FOR EACH ROW EXECUTE FUNCTION stockdc_prepare_phase8_observed_version('{dataset}');
          CREATE TRIGGER validate_{links} BEFORE INSERT ON {links} FOR EACH ROW
          EXECUTE FUNCTION stockdc_validate_phase8_observation('{dataset}','{versions}','{target}');
          CREATE TRIGGER immutable_{links} BEFORE UPDATE OR DELETE ON {links} FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();
          CREATE TRIGGER no_truncate_{links} BEFORE TRUNCATE ON {links} FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();
          INSERT INTO {links} ({target},raw_artifact_id,ingest_run_id)
          SELECT id,raw_artifact_id,ingest_run_id FROM {versions};""")
        op.execute(f"ALTER TABLE {versions} DISABLE TRIGGER immutable_{versions}")
        op.execute(f"""UPDATE {versions} AS v SET business_content_hash=encode(digest(convert_to(
          (to_jsonb(v)-ARRAY['id','business_content_hash','ingested_at','raw_artifact_id','ingest_run_id','source','security_id','market_index_id','trade_date','action_type','ex_date'])::text,'UTF8'),'sha256'),'hex')""")
        op.execute(f"ALTER TABLE {versions} ENABLE TRIGGER immutable_{versions}")


def downgrade() -> None:
    op.execute("DROP TRIGGER validate_phase8_publication_time ON publication_evidence")
    op.execute("DROP FUNCTION stockdc_validate_phase8_publication_time()")
    op.execute("DROP TRIGGER no_truncate_market_index ON market_index; DROP TRIGGER immutable_market_index ON market_index; DROP TRIGGER prepare_market_index_identity ON market_index; DROP FUNCTION stockdc_prepare_market_index_identity()")
    for dataset, versions, links, _ in reversed(DATASETS):
        op.execute(f"DROP TRIGGER no_truncate_{links} ON {links}; DROP TRIGGER immutable_{links} ON {links}; DROP TRIGGER validate_{links} ON {links}")
        op.execute(f"DROP TRIGGER prepare_{versions} ON {versions}; CREATE TRIGGER prepare_{versions} BEFORE INSERT ON {versions} FOR EACH ROW EXECUTE FUNCTION stockdc_prepare_observed_version('{dataset}')")
        op.execute(f"ALTER TABLE {versions} DISABLE TRIGGER immutable_{versions}")
        op.execute(f"""UPDATE {versions} AS v SET business_content_hash=encode(digest(convert_to(
          (to_jsonb(v)-ARRAY['id','business_content_hash','ingested_at','raw_artifact_id','ingest_run_id','source','security_id','market_index_id','trade_date'])::text,'UTF8'),'sha256'),'hex')""")
        op.execute(f"ALTER TABLE {versions} ENABLE TRIGGER immutable_{versions}")
        op.drop_table(links)
    op.execute("DROP FUNCTION stockdc_validate_phase8_observation(); DROP FUNCTION stockdc_prepare_phase8_observed_version()")
    for table, checks in reversed(tuple(CHECKS.items())):
        for name, _ in reversed(checks):
            op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
