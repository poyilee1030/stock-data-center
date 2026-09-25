#!/usr/bin/env python
"""Step 35-d-3: move a database built by the old migration chain onto the baseline.

The old chain's head (`5c1e8d2a7b90`) holds the v2 tables beside everything left
of v1. This drops the v1 part and records the baseline as the database's
revision, in one transaction that commits only if

- the database is at the old head (or already at the baseline: nothing to do),
- every v1 object drops without CASCADE, so nothing v2 depends on one,
- the result is, object by object, the schema a fresh baseline builds, and
- every v2 table holds the rows it held before.

What counts as v1 is what a fresh baseline does not build: the reference is a
scratch database on the same server, upgraded to head and dropped again.
Raw files under `data/raw/` are not touched.

Without `--execute` it only reports what it would drop and the v2 row counts.

    .venv/bin/python scripts/rebase_to_baseline.py \\
        --database-url postgresql+psycopg://stockdc:stockdc@localhost:26519/stockdc_backfill
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import uuid
from dataclasses import asdict, dataclass, field

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

ROOT = pathlib.Path(__file__).resolve().parents[1]
OLD_HEAD = "5c1e8d2a7b90"

# One query per kind of schema object in `public`, each row naming the object
# first. Extension members are left to their extension; row data, sizes and
# sequence positions are not schema.
SIGNATURE_QUERIES = {
    "tables": """
        SELECT c.relname, a.attnum, a.attname, format_type(a.atttypid, a.atttypmod),
               a.attnotnull, pg_get_expr(d.adbin, d.adrelid), a.attidentity, a.attgenerated
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
          JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
          LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
         WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')""",
    "constraints": """
        SELECT c.conrelid::regclass::text, c.conname, pg_get_constraintdef(c.oid)
          FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
         WHERE n.nspname = 'public'""",
    "indexes": """
        SELECT indexname, tablename, indexdef FROM pg_indexes WHERE schemaname = 'public'""",
    "triggers": """
        SELECT t.tgname, t.tgrelid::regclass::text, pg_get_triggerdef(t.oid)
          FROM pg_trigger t
          JOIN pg_class c ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND NOT t.tgisinternal""",
    "functions": """
        SELECT p.oid::regprocedure::text, pg_get_functiondef(p.oid)
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.prokind IN ('f', 'p')
           AND NOT EXISTS (SELECT 1 FROM pg_depend d
                            WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid
                              AND d.deptype = 'e')""",
    "views": """
        SELECT viewname, definition FROM pg_views WHERE schemaname = 'public'""",
    "sequences": """
        SELECT sequencename, data_type::text, start_value, increment_by
          FROM pg_sequences WHERE schemaname = 'public'""",
    "extensions": "SELECT extname, extversion FROM pg_extension",
    "types": """
        SELECT t.typname, t.typtype::text
          FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
         WHERE n.nspname = 'public' AND t.typtype IN ('e', 'd')
           AND NOT EXISTS (SELECT 1 FROM pg_depend d
                            WHERE d.classid = 'pg_type'::regclass AND d.objid = t.oid
                              AND d.deptype = 'e')""",
}


class RebaseRefused(RuntimeError):
    """The database is not one this script may move; nothing was changed."""


@dataclass
class Plan:
    revision: str
    tables: list[str] = field(default_factory=list)
    views: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    sequences: list[str] = field(default_factory=list)
    extensions: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)
    already_rebased: bool = False
    executed: bool = False


def schema_signature(connection: sa.Connection) -> dict[str, list[tuple]]:
    return {
        kind: sorted(tuple(str(v) for v in row) for row in connection.execute(sa.text(query)))
        for kind, query in SIGNATURE_QUERIES.items()
    }


def _names(signature: dict[str, list[tuple]], kind: str) -> set[str]:
    return {row[0] for row in signature[kind]}


def _function_name(signature_text: str) -> str:
    return signature_text.split("(", 1)[0]


def _alembic_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["database_url"] = url
    return config


def baseline_head() -> str:
    return ScriptDirectory.from_config(_alembic_config("postgresql://unused")).get_current_head()


def baseline_signature(url: str) -> dict[str, list[tuple]]:
    """The schema a fresh baseline builds, on a scratch database beside `url`."""
    server = make_url(url)
    name = f"stockdc_baseline_{uuid.uuid4().hex}"
    admin = sa.create_engine(server.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
        scratch = server.set(database=name).render_as_string(hide_password=False)
        try:
            command.upgrade(_alembic_config(scratch), "head")
            engine = sa.create_engine(scratch)
            try:
                with engine.connect() as connection:
                    return schema_signature(connection)
            finally:
                engine.dispose()
        finally:
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{name}"')
    finally:
        admin.dispose()


def _differences(actual: dict, reference: dict) -> list[str]:
    lines = []
    for kind in SIGNATURE_QUERIES:
        extra = sorted(set(actual[kind]) - set(reference[kind]))
        missing = sorted(set(reference[kind]) - set(actual[kind]))
        lines += [f"{kind}: unexpected {row}" for row in extra]
        lines += [f"{kind}: missing {row}" for row in missing]
    return lines


def _row_counts(connection: sa.Connection, tables: list[str]) -> dict[str, int]:
    return {
        table: connection.scalar(sa.text(f'SELECT count(*) FROM "{table}"'))
        for table in tables
    }


def _quoted(names: list[str]) -> str:
    return ", ".join(f'"{name}"' for name in names)


def rebase(url: str, *, execute: bool) -> Plan:
    reference = baseline_signature(url)
    head = baseline_head()
    v2_tables = sorted(_names(reference, "tables") - {"alembic_version"})
    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection, connection.begin():
            revision = connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
            if revision == head:
                differences = _differences(schema_signature(connection), reference)
                if differences:
                    raise RebaseRefused(
                        f"at the baseline {head} but not its schema:\n" + "\n".join(differences))
                return Plan(revision, row_counts=_row_counts(connection, v2_tables),
                            already_rebased=True)
            if revision != OLD_HEAD:
                raise RebaseRefused(
                    f"database is at revision {revision}; only the old head {OLD_HEAD} "
                    "can be rebased")

            current = schema_signature(connection)
            missing = {
                kind: sorted(_names(reference, kind) - _names(current, kind))
                for kind in SIGNATURE_QUERIES
            }
            if any(missing.values()):
                raise RebaseRefused(f"the old head lacks baseline objects: {missing}")

            def leftover(kind: str) -> list[str]:
                return sorted(_names(current, kind) - _names(reference, kind))

            tables, views = leftover("tables"), leftover("views")
            functions, extensions, types = (
                leftover("functions"), leftover("extensions"), leftover("types"))
            # A sequence a v1 table owns drops with it; only free-standing ones remain.
            owned = set(connection.scalars(sa.text(
                "SELECT s.relname FROM pg_class s JOIN pg_depend d ON d.objid = s.oid "
                "AND d.classid = 'pg_class'::regclass AND d.deptype IN ('a', 'i') "
                "JOIN pg_class t ON t.oid = d.refobjid WHERE s.relkind = 'S' "
                "AND t.relname = ANY(:tables)"), {"tables": tables}))
            sequences = [s for s in leftover("sequences") if s not in owned]
            before = _row_counts(connection, v2_tables)
            plan = Plan(
                revision, tables, views, [_function_name(f) for f in functions],
                sequences, extensions, types, before,
            )
            if not execute:
                connection.rollback()
                return plan

            connection.exec_driver_sql("SET LOCAL lock_timeout = '30s'")
            if views:
                connection.exec_driver_sql(f"DROP VIEW {_quoted(views)}")
            if tables:
                connection.exec_driver_sql(f"DROP TABLE {_quoted(tables)}")
            if sequences:
                connection.exec_driver_sql(f"DROP SEQUENCE {_quoted(sequences)}")
            if functions:
                connection.exec_driver_sql(f"DROP FUNCTION {', '.join(functions)}")
            if types:
                connection.exec_driver_sql(f"DROP TYPE {_quoted(types)}")
            if extensions:
                connection.exec_driver_sql(f"DROP EXTENSION {_quoted(extensions)}")
            connection.execute(
                sa.text("UPDATE alembic_version SET version_num = :head"), {"head": head})

            differences = _differences(schema_signature(connection), reference)
            if differences:
                raise RebaseRefused(
                    "after the drops the schema is not the baseline's:\n" + "\n".join(differences))
            after = _row_counts(connection, v2_tables)
            if after != before:
                raise RebaseRefused(f"v2 row counts changed: {before} -> {after}")
            plan.executed = True
        return plan
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--execute", action="store_true",
                        help="drop the v1 objects; without it only the plan is printed")
    args = parser.parse_args(argv)
    try:
        plan = rebase(args.database_url, execute=args.execute)
    except RebaseRefused as refused:
        print(f"refused, nothing changed: {refused}", file=sys.stderr)
        return 1
    print(json.dumps(asdict(plan), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
