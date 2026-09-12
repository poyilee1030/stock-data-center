"""implement final Phase 8 market-reference contracts

Revision ID: 6b4e8d1f2a73
Revises: 3f7c9a2d6e10
Create Date: 2026-09-12

Cache impact: none. No cache exists. Existing corporate-action rows receive
one conservative legacy event identity per revision because the old schema
cannot prove which rows represented corrections of one source event. Existing
index metadata is re-observed through a trusted migration run and retains the
underlying raw artifact; no historical publication time is invented.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "6b4e8d1f2a73"
down_revision: str | None = "3f7c9a2d6e10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OBSERVATIONS = (
    ("market_index", "market_index_versions", "market_index_version_observations", "market_index_version_id"),
    ("market_index_metadata", "market_index_metadata_versions", "market_index_metadata_version_observations", "market_index_metadata_version_id"),
    ("corporate_action", "corporate_action_versions", "corporate_action_version_observations", "corporate_action_version_id"),
    ("official_valuation", "official_valuation_versions", "official_valuation_version_observations", "official_valuation_version_id"),
)


CHECKS = {
    "market_index": (("market_index_code_nonempty", "index_code <> ''"),),
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


def _publication_function(*, include_metadata: bool) -> str:
    metadata_branch = """
    ELSIF NEW.market_index_metadata_version_id IS NOT NULL THEN
        target_dataset := 'market_index_metadata';
        SELECT source INTO target_source FROM market_index_metadata_versions
         WHERE id = NEW.market_index_metadata_version_id;""" if include_metadata else ""
    metadata_prior = ", prior.market_index_metadata_version_id" if include_metadata else ""
    metadata_new = ", NEW.market_index_metadata_version_id" if include_metadata else ""
    return f"""
CREATE OR REPLACE FUNCTION stockdc_prepare_publication_evidence() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE target_dataset text; target_source text; prior publication_evidence%ROWTYPE;
BEGIN
    NEW.recorded_at := statement_timestamp();
    IF NEW.security_metadata_version_id IS NOT NULL THEN target_dataset := 'security_metadata'; SELECT source INTO target_source FROM security_metadata_versions WHERE id=NEW.security_metadata_version_id;
    ELSIF NEW.daily_price_version_id IS NOT NULL THEN target_dataset := 'daily_price'; SELECT source INTO target_source FROM daily_price_versions WHERE id=NEW.daily_price_version_id;
    ELSIF NEW.monthly_revenue_version_id IS NOT NULL THEN target_dataset := 'monthly_revenue'; SELECT source INTO target_source FROM monthly_revenue_versions WHERE id=NEW.monthly_revenue_version_id;
    ELSIF NEW.financial_filing_version_id IS NOT NULL THEN target_dataset := 'financial_filing'; SELECT source INTO target_source FROM financial_filing_versions WHERE id=NEW.financial_filing_version_id;
    ELSIF NEW.tdcc_snapshot_version_id IS NOT NULL THEN target_dataset := 'tdcc_snapshot'; SELECT source INTO target_source FROM tdcc_snapshot_versions WHERE id=NEW.tdcc_snapshot_version_id;
    ELSIF NEW.institutional_investor_version_id IS NOT NULL THEN target_dataset := 'institutional_investor'; SELECT source INTO target_source FROM institutional_investor_versions WHERE id=NEW.institutional_investor_version_id;
    ELSIF NEW.foreign_holding_version_id IS NOT NULL THEN target_dataset := 'foreign_holding'; SELECT source INTO target_source FROM foreign_holding_versions WHERE id=NEW.foreign_holding_version_id;
    ELSIF NEW.institutional_market_summary_version_id IS NOT NULL THEN target_dataset := 'institutional_market_summary'; SELECT source INTO target_source FROM institutional_market_summary_versions WHERE id=NEW.institutional_market_summary_version_id;
    ELSIF NEW.margin_trading_version_id IS NOT NULL THEN target_dataset := 'margin_trading'; SELECT source INTO target_source FROM margin_trading_versions WHERE id=NEW.margin_trading_version_id;
    ELSIF NEW.securities_lending_version_id IS NOT NULL THEN target_dataset := 'securities_lending'; SELECT source INTO target_source FROM securities_lending_versions WHERE id=NEW.securities_lending_version_id;
    ELSIF NEW.market_index_version_id IS NOT NULL THEN target_dataset := 'market_index'; SELECT source INTO target_source FROM market_index_versions WHERE id=NEW.market_index_version_id;
    {metadata_branch}
    ELSIF NEW.corporate_action_version_id IS NOT NULL THEN target_dataset := 'corporate_action'; SELECT source INTO target_source FROM corporate_action_versions WHERE id=NEW.corporate_action_version_id;
    ELSIF NEW.official_valuation_version_id IS NOT NULL THEN target_dataset := 'official_valuation'; SELECT source INTO target_source FROM official_valuation_versions WHERE id=NEW.official_valuation_version_id;
    ELSIF NEW.security_tag_version_id IS NOT NULL THEN target_dataset := 'security_tag'; SELECT source INTO target_source FROM security_tag_versions WHERE id=NEW.security_tag_version_id;
    ELSIF NEW.xbrl_concept_catalog_version_id IS NOT NULL THEN target_dataset := 'xbrl_concept_catalog'; SELECT source INTO target_source FROM xbrl_concept_catalog_versions WHERE id=NEW.xbrl_concept_catalog_version_id;
    ELSE RAISE EXCEPTION 'publication evidence requires exactly one target' USING ERRCODE='23514';
    END IF;
    IF target_source IS NULL THEN RAISE EXCEPTION 'publication evidence target does not exist' USING ERRCODE='23503'; END IF;
    IF NEW.dataset_code <> target_dataset OR NEW.source <> target_source THEN RAISE EXCEPTION 'evidence dataset/source does not match target' USING ERRCODE='23514'; END IF;
    PERFORM stockdc_assert_lineage(NEW.raw_artifact_id, NEW.ingest_run_id, NEW.dataset_code, NEW.source);
    IF NEW.supersedes_evidence_id IS NOT NULL THEN
        SELECT * INTO prior FROM publication_evidence WHERE id=NEW.supersedes_evidence_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'superseded evidence does not exist' USING ERRCODE='23503'; END IF;
        IF prior.dataset_code <> NEW.dataset_code OR prior.source <> NEW.source OR
           jsonb_build_array(prior.security_metadata_version_id,prior.daily_price_version_id,prior.monthly_revenue_version_id,prior.financial_filing_version_id,prior.tdcc_snapshot_version_id,prior.institutional_investor_version_id,prior.foreign_holding_version_id,prior.institutional_market_summary_version_id,prior.margin_trading_version_id,prior.securities_lending_version_id,prior.market_index_version_id{metadata_prior},prior.corporate_action_version_id,prior.official_valuation_version_id,prior.security_tag_version_id,prior.xbrl_concept_catalog_version_id)
           IS DISTINCT FROM
           jsonb_build_array(NEW.security_metadata_version_id,NEW.daily_price_version_id,NEW.monthly_revenue_version_id,NEW.financial_filing_version_id,NEW.tdcc_snapshot_version_id,NEW.institutional_investor_version_id,NEW.foreign_holding_version_id,NEW.institutional_market_summary_version_id,NEW.margin_trading_version_id,NEW.securities_lending_version_id,NEW.market_index_version_id{metadata_new},NEW.corporate_action_version_id,NEW.official_valuation_version_id,NEW.security_tag_version_id,NEW.xbrl_concept_catalog_version_id)
        THEN RAISE EXCEPTION 'supersession must stay on the same target and source' USING ERRCODE='23514'; END IF;
    END IF;
    NEW.publication_evidence_hash := encode(digest(convert_to((to_jsonb(NEW)-ARRAY['id','publication_evidence_hash','recorded_at','raw_artifact_id','ingest_run_id'])::text,'UTF8'),'sha256'),'hex');
    RETURN NEW;
END; $$;
"""


def _create_observation_table(version_table: str, link_table: str, target: str) -> None:
    op.create_table(
        link_table,
        sa.Column(target, sa.BigInteger(), nullable=False),
        sa.Column("raw_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingest_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint([target], [f"{version_table}.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["raw_artifact_id", "ingest_run_id"],
            ["raw_artifact_observations.raw_artifact_id", "raw_artifact_observations.ingest_run_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(target, "raw_artifact_id", "ingest_run_id"),
    )


def upgrade() -> None:
    op.create_table(
        "corporate_action_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("security_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_event_key", sa.Text(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("statement_timestamp()")),
        sa.ForeignKeyConstraint(["security_id"], ["security.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("security_id", "source", "source_event_key", name="uq_corporate_action_event_source_key"),
        sa.CheckConstraint("source <> ''", name=op.f("ck_corporate_action_events_corporate_action_event_source_nonempty")),
        sa.CheckConstraint("source_event_key <> ''", name=op.f("ck_corporate_action_events_corporate_action_event_key_nonempty")),
    )
    op.add_column("corporate_action_versions", sa.Column("event_id", sa.BigInteger(), nullable=True))
    op.execute("ALTER TABLE corporate_action_versions DISABLE TRIGGER immutable_corporate_action_versions")
    op.execute("""
        INSERT INTO corporate_action_events (security_id,source,source_event_key,created_at)
        SELECT security_id,source,'legacy-version:'||id,ingested_at FROM corporate_action_versions;
        UPDATE corporate_action_versions v SET event_id=e.id
          FROM corporate_action_events e
         WHERE e.security_id=v.security_id AND e.source=v.source
           AND e.source_event_key='legacy-version:'||v.id;
    """)
    op.drop_constraint("uq_corporate_action_business_revision", "corporate_action_versions", type_="unique")
    op.drop_constraint("fk_corporate_action_versions_security_id_security", "corporate_action_versions", type_="foreignkey")
    op.drop_column("corporate_action_versions", "security_id")
    op.alter_column("corporate_action_versions", "event_id", nullable=False)
    op.create_foreign_key(op.f("fk_corporate_action_versions_event_id_corporate_action_events"), "corporate_action_versions", "corporate_action_events", ["event_id"], ["id"], ondelete="RESTRICT")
    op.create_unique_constraint("uq_corporate_action_business_revision", "corporate_action_versions", ["event_id", "source", "business_content_hash"])

    op.create_table(
        "market_index_metadata_versions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("market_index_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date()),
        sa.Column("market", sa.String(32), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("business_content_hash", sa.CHAR(64), nullable=False),
        sa.Column("ingested_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("raw_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingest_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["market_index_id"], ["market_index.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["raw_artifact_id", "ingest_run_id"], ["raw_artifact_observations.raw_artifact_id", "raw_artifact_observations.ingest_run_id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("market_index_id", "source", "effective_from", "business_content_hash", name="uq_market_index_metadata_business_revision"),
        sa.CheckConstraint("market <> ''", name=op.f("ck_market_index_metadata_versions_market_index_metadata_market_nonempty")),
        sa.CheckConstraint("name <> ''", name=op.f("ck_market_index_metadata_versions_market_index_metadata_name_nonempty")),
        sa.CheckConstraint("effective_to IS NULL OR effective_to >= effective_from", name=op.f("ck_market_index_metadata_versions_market_index_metadata_effective_range")),
    )

    for _, versions, links, target in OBSERVATIONS:
        _create_observation_table(versions, links, target)

    op.add_column("publication_evidence", sa.Column("market_index_metadata_version_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(op.f("fk_publication_evidence_market_index_metadata_version_id_market_index_metadata_versions"), "publication_evidence", "market_index_metadata_versions", ["market_index_metadata_version_id"], ["id"], ondelete="RESTRICT")
    op.drop_constraint(op.f("ck_publication_evidence_exactly_one_target"), "publication_evidence", type_="check")
    op.create_check_constraint(op.f("ck_publication_evidence_exactly_one_target"), "publication_evidence", "num_nonnulls(security_metadata_version_id,daily_price_version_id,monthly_revenue_version_id,financial_filing_version_id,tdcc_snapshot_version_id,institutional_investor_version_id,foreign_holding_version_id,institutional_market_summary_version_id,margin_trading_version_id,securities_lending_version_id,market_index_version_id,market_index_metadata_version_id,corporate_action_version_id,official_valuation_version_id,security_tag_version_id,xbrl_concept_catalog_version_id)=1")

    for table, checks in CHECKS.items():
        for name, condition in checks:
            op.create_check_constraint(op.f(f"ck_{table}_{name}"), table, condition)

    op.execute(_publication_function(include_metadata=True))
    op.execute(r"""
        CREATE FUNCTION stockdc_prepare_phase8_observed_version()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE content jsonb; event_source text;
        BEGIN
          PERFORM stockdc_assert_lineage(NEW.raw_artifact_id,NEW.ingest_run_id,TG_ARGV[0],NEW.source);
          IF TG_ARGV[0]='corporate_action' THEN
            SELECT source INTO event_source FROM corporate_action_events WHERE id=NEW.event_id;
            IF event_source IS NULL THEN RAISE EXCEPTION 'corporate action event missing' USING ERRCODE='23503'; END IF;
            IF event_source<>NEW.source THEN RAISE EXCEPTION 'corporate action event/source mismatch' USING ERRCODE='23514'; END IF;
          END IF;
          NEW.ingested_at:=statement_timestamp();
          content:=to_jsonb(NEW)-ARRAY['id','business_content_hash','ingested_at','raw_artifact_id','ingest_run_id','source','security_id','market_index_id','event_id','trade_date','effective_from'];
          NEW.business_content_hash:=encode(digest(convert_to(content::text,'UTF8'),'sha256'),'hex');
          RETURN NEW;
        END; $$;
        CREATE FUNCTION stockdc_prepare_phase8_identity()
        RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.created_at:=statement_timestamp(); RETURN NEW; END; $$;
        CREATE TRIGGER prepare_market_index_identity BEFORE INSERT ON market_index FOR EACH ROW EXECUTE FUNCTION stockdc_prepare_phase8_identity();
        CREATE TRIGGER immutable_market_index BEFORE UPDATE OR DELETE ON market_index FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();
        CREATE TRIGGER no_truncate_market_index BEFORE TRUNCATE ON market_index FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();
        CREATE TRIGGER prepare_corporate_action_event BEFORE INSERT ON corporate_action_events FOR EACH ROW EXECUTE FUNCTION stockdc_prepare_phase8_identity();
        CREATE TRIGGER immutable_corporate_action_events BEFORE UPDATE OR DELETE ON corporate_action_events FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();
        CREATE TRIGGER no_truncate_corporate_action_events BEFORE TRUNCATE ON corporate_action_events FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();

        CREATE FUNCTION stockdc_validate_phase8_observation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE vid bigint; version_source text; run_dataset text; run_source text;
        BEGIN
          vid:=(to_jsonb(NEW)->>TG_ARGV[2])::bigint;
          EXECUTE format('SELECT source FROM %I WHERE id=$1',TG_ARGV[1]) INTO version_source USING vid;
          SELECT dataset_code,source INTO run_dataset,run_source FROM ingest_runs WHERE id=NEW.ingest_run_id;
          IF version_source IS NULL OR run_dataset IS NULL THEN RAISE EXCEPTION 'Phase 8 observation target/run missing' USING ERRCODE='23503'; END IF;
          IF run_dataset<>TG_ARGV[0] OR run_source<>version_source THEN RAISE EXCEPTION 'Phase 8 observation dataset/source mismatch' USING ERRCODE='23514'; END IF;
          RETURN NEW;
        END; $$;
        CREATE FUNCTION stockdc_validate_phase8_publication_time()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE lower_date date;
        BEGIN
          IF NEW.published_at IS NULL THEN RETURN NEW; END IF;
          IF NEW.market_index_version_id IS NOT NULL THEN SELECT trade_date INTO lower_date FROM market_index_versions WHERE id=NEW.market_index_version_id;
          ELSIF NEW.market_index_metadata_version_id IS NOT NULL THEN SELECT effective_from INTO lower_date FROM market_index_metadata_versions WHERE id=NEW.market_index_metadata_version_id;
          ELSIF NEW.official_valuation_version_id IS NOT NULL THEN SELECT trade_date INTO lower_date FROM official_valuation_versions WHERE id=NEW.official_valuation_version_id;
          ELSIF NEW.corporate_action_version_id IS NOT NULL THEN SELECT announcement_date INTO lower_date FROM corporate_action_versions WHERE id=NEW.corporate_action_version_id;
          ELSE RETURN NEW; END IF;
          IF lower_date IS NOT NULL AND (NEW.published_at AT TIME ZONE 'Asia/Taipei')::date<lower_date THEN RAISE EXCEPTION 'publication precedes source date' USING ERRCODE='23514'; END IF;
          RETURN NEW;
        END; $$;
        CREATE TRIGGER validate_phase8_publication_time BEFORE INSERT ON publication_evidence FOR EACH ROW EXECUTE FUNCTION stockdc_validate_phase8_publication_time();
    """)

    for dataset, versions, links, target in OBSERVATIONS:
        if versions != "market_index_metadata_versions":
            op.execute(f"DROP TRIGGER prepare_{versions} ON {versions}")
        op.execute(f"CREATE TRIGGER prepare_{versions} BEFORE INSERT ON {versions} FOR EACH ROW EXECUTE FUNCTION stockdc_prepare_phase8_observed_version('{dataset}')")
        if versions == "market_index_metadata_versions":
            op.execute(f"CREATE TRIGGER immutable_{versions} BEFORE UPDATE OR DELETE ON {versions} FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation(); CREATE TRIGGER no_truncate_{versions} BEFORE TRUNCATE ON {versions} FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation()")
        op.execute(f"CREATE TRIGGER validate_{links} BEFORE INSERT ON {links} FOR EACH ROW EXECUTE FUNCTION stockdc_validate_phase8_observation('{dataset}','{versions}','{target}')")
        op.execute(f"CREATE TRIGGER immutable_{links} BEFORE UPDATE OR DELETE ON {links} FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation(); CREATE TRIGGER no_truncate_{links} BEFORE TRUNCATE ON {links} FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation()")

    op.execute(r"""
        INSERT INTO dataset_catalog(dataset_code,description,schema_version)
        VALUES('market_index_metadata','effective-dated market index metadata','v1')
        ON CONFLICT(dataset_code) DO NOTHING;
        INSERT INTO dataset_sources(dataset_code,source,supports_market_pit,supports_system_pit,publication_time_quality,evidence_status,accepted_evidence_types,is_canonical)
        SELECT 'market_index_metadata',source,supports_market_pit,supports_system_pit,publication_time_quality,evidence_status,accepted_evidence_types,is_canonical
          FROM dataset_sources WHERE dataset_code='market_index'
        ON CONFLICT(dataset_code,source) DO NOTHING;
        DO $$
        DECLARE r record; new_run uuid; metadata_id bigint;
        BEGIN
          IF EXISTS(SELECT 1 FROM market_index i WHERE NOT EXISTS(SELECT 1 FROM market_index_versions v WHERE v.market_index_id=i.id)) THEN
            RAISE EXCEPTION 'cannot migrate index metadata without retained index-version provenance';
          END IF;
          FOR r IN
            SELECT DISTINCT ON(i.id,v.source) i.id index_id,i.market,i.name,v.source,v.trade_date,v.raw_artifact_id,o.source_uri
              FROM market_index i JOIN market_index_versions v ON v.market_index_id=i.id
              JOIN raw_artifact_observations o ON o.raw_artifact_id=v.raw_artifact_id AND o.ingest_run_id=v.ingest_run_id
             ORDER BY i.id,v.source,v.trade_date,v.id
          LOOP
            INSERT INTO ingest_runs(dataset_code,source,status,started_at,completed_at,run_metadata)
            VALUES('market_index_metadata',r.source,'succeeded',statement_timestamp(),statement_timestamp(),jsonb_build_object('migration','6b4e8d1f2a73')) RETURNING id INTO new_run;
            INSERT INTO raw_artifact_observations(raw_artifact_id,ingest_run_id,source_uri,fetched_at)
            VALUES(r.raw_artifact_id,new_run,r.source_uri,statement_timestamp());
            INSERT INTO market_index_metadata_versions(market_index_id,source,effective_from,market,name,business_content_hash,ingested_at,raw_artifact_id,ingest_run_id)
            VALUES(r.index_id,r.source,r.trade_date,r.market,r.name,repeat('0',64),statement_timestamp(),r.raw_artifact_id,new_run) RETURNING id INTO metadata_id;
            INSERT INTO market_index_metadata_version_observations VALUES(metadata_id,r.raw_artifact_id,new_run);
            INSERT INTO publication_evidence(dataset_code,source,evidence_kind,published_at,evidence_source,evidence_type,quality_rank,market_index_metadata_version_id,publication_evidence_hash,recorded_at,raw_artifact_id,ingest_run_id)
            VALUES('market_index_metadata',r.source,'unknown',NULL,'phase8 trusted migration','official',0,metadata_id,repeat('0',64),statement_timestamp(),r.raw_artifact_id,new_run);
          END LOOP;
        END $$;
        ALTER TABLE market_index DROP COLUMN market, DROP COLUMN name;
    """)

    for _, versions, links, target in OBSERVATIONS:
        if versions != "market_index_metadata_versions":
            op.execute(f"INSERT INTO {links}({target},raw_artifact_id,ingest_run_id) SELECT id,raw_artifact_id,ingest_run_id FROM {versions}")
        op.execute(f"ALTER TABLE {versions} DISABLE TRIGGER immutable_{versions}")
        op.execute(f"UPDATE {versions} v SET business_content_hash=encode(digest(convert_to((to_jsonb(v)-ARRAY['id','business_content_hash','ingested_at','raw_artifact_id','ingest_run_id','source','security_id','market_index_id','event_id','trade_date','effective_from'])::text,'UTF8'),'sha256'),'hex')")
        op.execute(f"ALTER TABLE {versions} ENABLE TRIGGER immutable_{versions}")

    op.create_index("ix_market_index_metadata_pit", "market_index_metadata_versions", ["market_index_id","source","effective_from","ingested_at"])
    op.create_index("ix_corporate_action_pit", "corporate_action_versions", ["event_id","source","ingested_at"])


def downgrade() -> None:
    op.drop_index("ix_corporate_action_pit", table_name="corporate_action_versions")
    op.drop_index("ix_market_index_metadata_pit", table_name="market_index_metadata_versions")
    op.add_column("market_index", sa.Column("market", sa.String(32), nullable=True))
    op.add_column("market_index", sa.Column("name", sa.Text(), nullable=True))
    op.execute("ALTER TABLE market_index DISABLE TRIGGER immutable_market_index")
    op.execute("""
        UPDATE market_index i SET market=m.market,name=m.name FROM (
          SELECT DISTINCT ON(market_index_id) market_index_id,market,name
          FROM market_index_metadata_versions ORDER BY market_index_id,effective_from DESC,id DESC
        ) m WHERE m.market_index_id=i.id
    """)
    op.execute("ALTER TABLE market_index ENABLE TRIGGER immutable_market_index")
    op.alter_column("market_index", "market", nullable=False)
    op.alter_column("market_index", "name", nullable=False)

    op.execute("DROP TRIGGER validate_phase8_publication_time ON publication_evidence; DROP FUNCTION stockdc_validate_phase8_publication_time()")
    op.execute("""
        ALTER TABLE publication_evidence_observations DISABLE TRIGGER immutable_publication_evidence_observations;
        ALTER TABLE publication_evidence DISABLE TRIGGER immutable_publication_evidence;
        DELETE FROM publication_evidence_observations o USING publication_evidence e
         WHERE o.publication_evidence_id=e.id AND e.market_index_metadata_version_id IS NOT NULL;
        DELETE FROM publication_evidence WHERE market_index_metadata_version_id IS NOT NULL;
        ALTER TABLE publication_evidence ENABLE TRIGGER immutable_publication_evidence;
        ALTER TABLE publication_evidence_observations ENABLE TRIGGER immutable_publication_evidence_observations;
    """)
    op.drop_constraint(op.f("ck_publication_evidence_exactly_one_target"), "publication_evidence", type_="check")
    op.drop_constraint(op.f("fk_publication_evidence_market_index_metadata_version_id_market_index_metadata_versions"), "publication_evidence", type_="foreignkey")
    op.drop_column("publication_evidence", "market_index_metadata_version_id")
    op.create_check_constraint(op.f("ck_publication_evidence_exactly_one_target"), "publication_evidence", "num_nonnulls(security_metadata_version_id,daily_price_version_id,monthly_revenue_version_id,financial_filing_version_id,tdcc_snapshot_version_id,institutional_investor_version_id,foreign_holding_version_id,institutional_market_summary_version_id,margin_trading_version_id,securities_lending_version_id,market_index_version_id,corporate_action_version_id,official_valuation_version_id,security_tag_version_id,xbrl_concept_catalog_version_id)=1")
    op.execute(_publication_function(include_metadata=False))

    for dataset, versions, links, _ in reversed(OBSERVATIONS):
        op.execute(f"DROP TRIGGER no_truncate_{links} ON {links}; DROP TRIGGER immutable_{links} ON {links}; DROP TRIGGER validate_{links} ON {links}")
        if versions != "market_index_metadata_versions":
            op.execute(f"DROP TRIGGER prepare_{versions} ON {versions}; CREATE TRIGGER prepare_{versions} BEFORE INSERT ON {versions} FOR EACH ROW EXECUTE FUNCTION stockdc_prepare_observed_version('{dataset}')")
            if versions != "corporate_action_versions":
                op.execute(f"ALTER TABLE {versions} DISABLE TRIGGER immutable_{versions}")
                op.execute(f"UPDATE {versions} v SET business_content_hash=encode(digest(convert_to((to_jsonb(v)-ARRAY['id','business_content_hash','ingested_at','raw_artifact_id','ingest_run_id','source','security_id','market_index_id','trade_date'])::text,'UTF8'),'sha256'),'hex')")
                op.execute(f"ALTER TABLE {versions} ENABLE TRIGGER immutable_{versions}")
        op.drop_table(links)

    op.execute("DROP TRIGGER no_truncate_market_index_metadata_versions ON market_index_metadata_versions; DROP TRIGGER immutable_market_index_metadata_versions ON market_index_metadata_versions; DROP TRIGGER prepare_market_index_metadata_versions ON market_index_metadata_versions; DROP TRIGGER no_truncate_market_index ON market_index; DROP TRIGGER immutable_market_index ON market_index; DROP TRIGGER prepare_market_index_identity ON market_index; DROP TRIGGER no_truncate_corporate_action_events ON corporate_action_events; DROP TRIGGER immutable_corporate_action_events ON corporate_action_events; DROP TRIGGER prepare_corporate_action_event ON corporate_action_events; DROP FUNCTION stockdc_prepare_phase8_identity(); DROP FUNCTION stockdc_validate_phase8_observation(); DROP FUNCTION stockdc_prepare_phase8_observed_version()")

    op.drop_constraint("uq_corporate_action_business_revision", "corporate_action_versions", type_="unique")
    op.add_column("corporate_action_versions", sa.Column("security_id", sa.BigInteger(), nullable=True))
    op.execute("ALTER TABLE corporate_action_versions DISABLE TRIGGER immutable_corporate_action_versions")
    op.execute("UPDATE corporate_action_versions v SET security_id=e.security_id FROM corporate_action_events e WHERE e.id=v.event_id")
    op.alter_column("corporate_action_versions", "security_id", nullable=False)
    op.create_foreign_key(op.f("fk_corporate_action_versions_security_id_security"), "corporate_action_versions", "security", ["security_id"], ["id"], ondelete="RESTRICT")
    op.create_unique_constraint("uq_corporate_action_business_revision", "corporate_action_versions", ["security_id","source","action_type","ex_date","business_content_hash"])
    op.drop_constraint(op.f("fk_corporate_action_versions_event_id_corporate_action_events"), "corporate_action_versions", type_="foreignkey")
    op.drop_column("corporate_action_versions", "event_id")
    op.execute("ALTER TABLE corporate_action_versions DISABLE TRIGGER immutable_corporate_action_versions")
    op.execute("UPDATE corporate_action_versions v SET business_content_hash=encode(digest(convert_to((to_jsonb(v)-ARRAY['id','business_content_hash','ingested_at','raw_artifact_id','ingest_run_id','source','security_id','market_index_id','trade_date'])::text,'UTF8'),'sha256'),'hex')")
    op.execute("ALTER TABLE corporate_action_versions ENABLE TRIGGER immutable_corporate_action_versions")
    op.create_index("ix_corporate_action_pit", "corporate_action_versions", ["security_id","source","ex_date","ingested_at"])
    op.drop_table("market_index_metadata_versions")
    op.drop_table("corporate_action_events")
    for table, checks in reversed(tuple(CHECKS.items())):
        for name, _ in reversed(checks):
            op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
