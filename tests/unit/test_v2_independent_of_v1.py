"""Step 35-d-1: schema v2 runs without the v1 code, so 35-d-2 can delete it.

Every v2 module is imported in a fresh interpreter, and every
`stock_data_center` module that loads must be one 35-d-2 keeps: the v2
package, the v2 schema and its shared column types, the source adapters with
the request, observation and parser types they produce, the HTTP fetcher and
the raw store. A v1 writer, service, resolver, evidence policy or table
declaration loading here would be deleted from under v2.
"""

from __future__ import annotations

import json
import pkgutil
import subprocess
import sys

import stock_data_center.v2

KEPT = (
    "stock_data_center",
    "stock_data_center.v2",
    "stock_data_center.db",
    "stock_data_center.db.base",
    "stock_data_center.db.schema_v2",
    "stock_data_center.provenance",
    "stock_data_center.ingestion",
    "stock_data_center.ingestion.adapters",
    "stock_data_center.ingestion.http",
    "stock_data_center.ingestion.ixbrl",
    "stock_data_center.ingestion.models",
    "stock_data_center.ingestion.observations",
    "stock_data_center.ingestion.raw_storage",
)


def _kept(module: str) -> bool:
    return module in KEPT or module.startswith(
        ("stock_data_center.v2.", "stock_data_center.ingestion.adapters.")
    )


def _loaded_by_v2() -> set[str]:
    modules = [
        f"stock_data_center.v2.{info.name}"
        for info in pkgutil.iter_modules(stock_data_center.v2.__path__)
    ]
    script = (
        "import importlib, json, sys\n"
        f"for name in {modules!r}: importlib.import_module(name)\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('stock_data_center'))))"
    )
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                          check=True)
    return set(json.loads(done.stdout))


def test_v2_loads_no_v1_module() -> None:
    assert sorted(m for m in _loaded_by_v2() if not _kept(m)) == []


def test_the_v2_tables_are_declared_without_the_v1_ones() -> None:
    script = (
        "import json\n"
        "from stock_data_center.db import schema_v2\n"
        "print(json.dumps(sorted(schema_v2.metadata.tables)))"
    )
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                          check=True)
    tables = set(json.loads(done.stdout))
    assert "daily_prices" in tables and "corporate_actions" in tables
    assert not tables & {"security", "publication_evidence", "daily_price_versions"}


# Step 35-d-2 deletes the v1 code; `db.metadata` declares the v1 tables that the
# migration chain and `alembic check` still see, and goes with them in 35-d-3.
PENDING_35D3 = ("stock_data_center.db.metadata",)
# Adapters v2 does not use: the Step 9 per-security pilot, the security
# lifecycle and metadata feeds the ISIN list replaced (ADR-0026), and the two
# legacy-archive readers whose history 35-c-1 migrated.
DELETED_ADAPTERS = ("daily_market", "security_lifecycle", "security_metadata",
                    "monthly_revenue_archive", "financial_filing_archive")


def test_src_holds_only_what_v2_keeps() -> None:
    import pathlib

    root = pathlib.Path(stock_data_center.v2.__path__[0]).parents[1]
    modules = {
        ".".join(path.relative_to(root).with_suffix("").parts).removesuffix(".__init__")
        for path in (root / "stock_data_center").rglob("*.py")
    }
    stray = sorted(m for m in modules if not _kept(m) and m not in PENDING_35D3)
    assert stray == []
    assert not {f"stock_data_center.ingestion.adapters.{a}" for a in DELETED_ADAPTERS} & modules
