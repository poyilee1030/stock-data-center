"""protect security identity

Revision ID: d81b5c9a3f20
Revises: 1e79e2e769c1
Create Date: 2026-09-11

Cache impact: none. This migration prevents mutation of stable identity and
does not alter PIT resolver, response, or derivation semantics.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "d81b5c9a3f20"
down_revision: Union[str, Sequence[str], None] = "1e79e2e769c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_security_security_code_nonempty",
        "security",
        "security_code <> ''",
    )
    op.create_check_constraint(
        "ck_security_market_nonempty",
        "security",
        "market <> ''",
    )
    op.execute(
        """
        CREATE FUNCTION stockdc_prepare_security_identity() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            NEW.created_at := statement_timestamp();
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER prepare_security_identity
        BEFORE INSERT ON security
        FOR EACH ROW EXECUTE FUNCTION stockdc_prepare_security_identity();

        CREATE TRIGGER immutable_security_identity
        BEFORE UPDATE OR DELETE ON security
        FOR EACH ROW EXECUTE FUNCTION stockdc_reject_mutation();

        CREATE TRIGGER no_truncate_security_identity
        BEFORE TRUNCATE ON security
        FOR EACH STATEMENT EXECUTE FUNCTION stockdc_reject_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS no_truncate_security_identity ON security;
        DROP TRIGGER IF EXISTS immutable_security_identity ON security;
        DROP TRIGGER IF EXISTS prepare_security_identity ON security;
        DROP FUNCTION IF EXISTS stockdc_prepare_security_identity();
        """
    )
    op.drop_constraint("ck_security_market_nonempty", "security", type_="check")
    op.drop_constraint(
        "ck_security_security_code_nonempty", "security", type_="check"
    )
