#!/usr/bin/env python
"""Reconcile the imported financial statements against legacy `stock_db`.

Step 23-c, following the earlier reconciliations (CLAUDE.md §78): coverage
first, then every stored value compared row by row with each difference
classified, and finally the derived quarterly table legacy built on top.

The join is the one ROADMAP §23 asks for — 會計科目代碼 ↔ concept QName — and
it works because every one of the 16.2 million facts in the three statements
carries an account code (audit §4.8). Legacy's row identity is
`(symbol, quarter, statement, account_code, period, period_type)`; ours is the
fact's own context. They line up like this:

    legacy period_type   our context
    as_of                instant_date = the quarter end
    accumulated          period_start = 1 January, period_end = quarter end
    quarter              period_start = the quarter start

**Units.** Legacy stored the printed number and left the 仟元 multiplier to its
consumers, which applied ×1,000 to whole tables. We apply the document's own
`scale` and `unitRef` (CLAUDE.md §72), so the comparison is unit-aware:

    iso4217:TWD              ours = legacy × 1,000   (the statements print 仟元)
    xbrli:shares             ours = legacy
    iso4217:TWD/xbrli:shares ours = legacy           (EPS, printed as filed)

The share-count rows are the documented difference Step 23-b found: legacy's
own consumers multiply codes 3997/3998/3999 by 1,000 although they are share
counts, so legacy's *normalized* figure is 1,000 times ours. That is a
PIT-correct difference, not a regression, and it is counted here rather than
hidden — `legacy_scaled_a_share_count` in the report.

**Coverage.** Legacy holds filings v1 excludes by scope: financial-industry
issuers and filers outside 上市/上櫃. Those are named rather than counted as
gaps. Legacy also stores only the current period, so our prior-year
comparatives have no legacy counterpart by construction and are excluded from
the comparison instead of reported as extra.

Usage:

    python scripts/reconcile_financial_statements.py \
        --database-url postgresql+psycopg://…/stockdc_backfill \
        --legacy-database-url postgresql+psycopg://…/stock_db
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

import sqlalchemy as sa

STATEMENTS = {
    "balance_sheet": "balance_sheet_xbrl",
    "income_statement": "income_statement_xbrl",
    "cash_flow": "cash_flow_xbrl",
}

# What our stored value is, per unit, in terms of the number legacy stored.
UNIT_MULTIPLIER = {
    "iso4217:TWD": Decimal(1000),
    "xbrli:shares": Decimal(1),
    "iso4217:TWD/xbrli:shares": Decimal(1),
}

# The `quarterly_reports_xbrl` columns that are a value the filing printed.
# Everything else in that table is derived — the `_q` columns are differences
# of year-to-date figures, the `_yoy` columns are ratios, and the four balance
# ratios are formulas — and belongs to Step 26, not here.
QUARTERLY_COLUMNS = {
    "revenue_acc": ("income_statement", "4000", "accumulated"),
    "op_income_acc": ("income_statement", "6900", "accumulated"),
    "non_op_income_acc": ("income_statement", "7000", "accumulated"),
    "pretax_income_acc": ("income_statement", "7900", "accumulated"),
    "eps_acc": ("income_statement", "9750", "accumulated"),
    "capital": ("balance_sheet", "3110", "as_of"),
}
NET_INCOME_CODE = {"consolidated": "8610", "individual": "8200"}

TOLERANCE = Decimal("0.0000000001")


def quarter_end(label: str) -> date:
    year, quarter = int(label[:4]), int(label[5])
    month, day = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}[quarter]
    return date(year, month, day)


def quarter_start(label: str) -> date:
    year, quarter = int(label[:4]), int(label[5])
    return date(year, (quarter - 1) * 3 + 1, 1)


def quarters(start: str, end: str) -> list[str]:
    labels = [
        f"{year}Q{quarter}"
        for year in range(int(start[:4]), int(end[:4]) + 1)
        for quarter in (1, 2, 3, 4)
    ]
    return [label for label in labels if start <= label <= end]


OUR_FACTS = sa.text(
    """
    SELECT s.security_code AS symbol,
           v.report_category,
           f.statement,
           f.account_code,
           f.unit_identity,
           f.numeric_value,
           f.period_start
      FROM financial_facts f
      JOIN financial_filing_versions v ON v.id = f.filing_version_id
      JOIN financial_filing_seals seal ON seal.filing_version_id = v.id
      JOIN security s ON s.id = v.security_id
     WHERE v.report_year = :year
       AND v.report_quarter = :quarter
       AND f.numeric_value IS NOT NULL
       AND (
             (f.statement = 'balance_sheet' AND f.instant_date = :quarter_end)
          OR (f.statement <> 'balance_sheet' AND f.period_end = :quarter_end)
       )
    """
)


def our_quarter(connection, label: str) -> tuple[dict, dict, dict]:
    """One quarter's current-period facts, keyed the way legacy keys its rows.

    Only the current period. Legacy stored no comparatives, so our prior-year
    columns have nothing to be compared against, and counting them as
    `absent_from_legacy` would report the same 40,000 rows every quarter.

    One quarter at a time because the whole window is 16.2 million facts: the
    comparison is per quarter and only the counters cross quarters.
    """

    ends = quarter_end(label)
    rows = connection.execute(
        OUR_FACTS,
        {
            "year": int(label[:4]),
            "quarter": int(label[5]),
            "quarter_end": ends,
            "year_start": date(ends.year, 1, 1),
            "quarter_start": quarter_start(label),
        },
    ).mappings()
    year_start = date(ends.year, 1, 1)
    starts = quarter_start(label)
    facts: dict = {}
    units: dict = {}
    categories: dict = {}
    for row in rows:
        # Which labels legacy filed this duration under, per statement.
        # The balance sheet is an instant. The cash-flow statement is only
        # ever year to date there (`period_type` has one value in that table).
        # The income statement is both — and in Q1 the single quarter *is* the
        # year to date, so one fact answers to both labels. Matching only one
        # would leave every Q1 `quarter` row of legacy's unmatched, which is
        # 57,000 rows reported as missing that are not.
        if row["statement"] == "balance_sheet":
            period_types: tuple[str, ...] = ("as_of",)
        elif row["statement"] == "cash_flow":
            period_types = (
                ("accumulated",) if row["period_start"] == year_start else ()
            )
        else:
            period_types = tuple(
                name
                for name, start in (
                    ("accumulated", year_start), ("quarter", starts)
                )
                if row["period_start"] == start
            )
        if not period_types:
            # A duration that is neither: the document's own comparative
            # columns, which legacy never held.
            continue
        for period_type in period_types:
            key = (
                row["symbol"], row["statement"], row["account_code"],
                period_type,
            )
            facts.setdefault(key, []).append(row["numeric_value"])
            units[key] = row["unit_identity"]
        categories[row["symbol"]] = row["report_category"]
    return facts, units, categories


def legacy_quarter(connection, label: str) -> dict:
    facts: dict = {}
    for statement, table in STATEMENTS.items():
        rows = connection.execute(
            sa.text(
                f"""
                SELECT symbol, account_code, period_type, value_num
                  FROM {table}
                 WHERE date = :quarter
                   AND value_num IS NOT NULL
                """  # noqa: S608 - the table name comes from STATEMENTS
            ),
            {"quarter": label},
        ).mappings()
        for row in rows:
            key = (
                row["symbol"], statement, row["account_code"],
                row["period_type"],
            )
            facts.setdefault(key, []).append(Decimal(str(row["value_num"])))
    return facts


def compare_values(
    *, unit: str, mine: Decimal, theirs: Decimal, account_code: str
) -> str | None:
    """`None` when they agree; otherwise the class of the difference."""
    multiplier = UNIT_MULTIPLIER.get(unit)
    if multiplier is None:
        return f"unknown_unit:{unit}"
    expected = theirs * multiplier
    if abs(expected - mine) <= TOLERANCE * max(abs(mine), Decimal(1)):
        return None
    return "value_differs"


def reconcile_quarter(
    *,
    mine: dict,
    units: dict,
    theirs: dict,
    codebook: frozenset[str],
    classes: Counter,
    examples: dict,
) -> None:
    """Compare one quarter, naming why each unmatched row is unmatched.

    Two whole classes of row cannot match and both are legacy's own shape:

    * a filing legacy holds and v1 excludes — a financial-industry issuer or a
      filer outside 上市/上櫃 — contributes every one of its rows;
    * a code outside legacy's `xbrl_codebook`, which its importer filtered on.
      The 1,040 cash-flow subtotals (`AA0000`, `AB0000`, `AC0100`–`AC0500`) are
      real rows the documents print and legacy dropped (Step 23-b).

    Anything else unmatched is a finding.
    """
    held = {key[0] for key in mine}
    for key, values in mine.items():
        if len(values) > 1:
            classes["duplicate_identity_in_ours"] += 1
            _example(examples, "duplicate_identity_in_ours", list(key), limit=5)
            continue
        legacy_values = theirs.get(key)
        if legacy_values is None:
            name = (
                "absent_from_legacy:inside_legacy_codebook"
                if key[2] in codebook
                else "absent_from_legacy:outside_legacy_codebook"
            )
            classes[name] += 1
            _example(examples, name, list(key), limit=10)
            continue
        verdict = compare_values(
            unit=units[key], mine=values[0], theirs=legacy_values[0],
            account_code=key[2],
        )
        if verdict is None:
            classes["identical"] += 1
            if units[key] == "xbrli:shares":
                # Step 23-b's fixture, kept visible. The printed numbers agree;
                # what differs is downstream. These are 待註銷股本 share counts
                # (3997/3998/3999 and the like), and legacy's consumers apply
                # the 仟元 multiplier to whole tables, so legacy's *normalized*
                # figure is 1,000 times ours. A documented, PIT-correct
                # difference (CLAUDE.md §72, §78), not a regression.
                classes["identical:share_count_legacy_consumers_scale"] += 1
                _example(
                    examples, "identical:share_count_legacy_consumers_scale",
                    {"key": list(key), "printed": str(values[0])}, limit=5,
                )
            continue
        classes[verdict] += 1
        _example(
            examples, verdict,
            {"key": list(key), "ours": str(values[0]),
             "legacy": str(legacy_values[0]), "unit": units[key]},
            limit=20,
        )
    for key in theirs:
        if key not in mine:
            name = (
                "absent_from_ours:row_missing_from_a_filing_we_hold"
                if key[0] in held
                else "absent_from_ours:filing_outside_v1_scope"
            )
            classes[name] += 1
            _example(examples, name, list(key), limit=10)


def _example(examples: dict, name: str, item, *, limit: int) -> None:
    if len(examples[name]) < limit:
        examples[name].append(item)


def reconcile_quarterly_row(
    *, row, mine: dict, units: dict, categories: dict,
    classes: Counter, examples: dict,
) -> None:
    category = categories.get(row["symbol"])
    if category is None:
        classes["filing_absent_from_ours"] += 1
        return
    if category != row["report_category"]:
        classes["report_category_differs"] += 1
        _example(
            examples, "report_category_differs",
            {"symbol": row["symbol"], "ours": category,
             "legacy": row["report_category"]},
            limit=10,
        )
        return
    columns = dict(QUARTERLY_COLUMNS)
    columns["net_income_acc"] = (
        "income_statement", NET_INCOME_CODE[category], "accumulated"
    )
    for column, (statement, code, period_type) in columns.items():
        theirs = row[column]
        key = (row["symbol"], statement, code, period_type)
        values = mine.get(key)
        if theirs is None and values is None:
            classes[f"{column}:both_absent"] += 1
            continue
        if theirs is None:
            classes[f"{column}:absent_from_legacy"] += 1
            continue
        if values is None:
            classes[f"{column}:absent_from_ours"] += 1
            _example(examples, f"{column}:absent_from_ours", list(key), limit=5)
            continue
        verdict = compare_values(
            unit=units[key], mine=values[0], theirs=Decimal(str(theirs)),
            account_code=code,
        )
        classes[f"{column}:" + (verdict or "identical")] += 1
        if verdict is not None:
            _example(
                examples, column,
                {"key": list(key), "ours": str(values[0]),
                 "legacy": str(theirs)},
                limit=5,
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reconcile-financial-statements")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument(
        "--legacy-database-url", default=os.getenv("LEGACY_DATABASE_URL")
    )
    parser.add_argument("--start", default="2020Q1")
    parser.add_argument("--end", default="2026Q2")
    args = parser.parse_args(argv)
    if not args.database_url or not args.legacy_database_url:
        parser.error("--database-url and --legacy-database-url are required")

    engine = sa.create_engine(args.database_url)
    legacy_engine = sa.create_engine(args.legacy_database_url)

    fact_classes: Counter = Counter()
    fact_examples: dict = defaultdict(list)
    quarterly_classes: Counter = Counter()
    quarterly_examples: dict = defaultdict(list)
    coverage: dict = {
        "filings_ours": 0, "filings_legacy": 0, "in_both": 0,
        "only_ours": 0, "only_legacy": 0,
    }
    only_ours_examples: list = []
    only_legacy_examples: list = []
    per_quarter: list = []

    with engine.connect() as connection, legacy_engine.connect() as legacy:
        codebook = frozenset(
            legacy.scalars(sa.text("SELECT account_code FROM xbrl_codebook"))
        )
        for label in quarters(args.start, args.end):
            mine, units, categories = our_quarter(connection, label)
            theirs = legacy_quarter(legacy, label)
            ours_filings = {key[0] for key in mine}
            legacy_filings = {key[0] for key in theirs}
            coverage["filings_ours"] += len(ours_filings)
            coverage["filings_legacy"] += len(legacy_filings)
            coverage["in_both"] += len(ours_filings & legacy_filings)
            coverage["only_ours"] += len(ours_filings - legacy_filings)
            coverage["only_legacy"] += len(legacy_filings - ours_filings)
            only_ours_examples.extend(
                f"{label}:{code}" for code in sorted(ours_filings - legacy_filings)[:5]
            )
            only_legacy_examples.extend(
                f"{label}:{code}" for code in sorted(legacy_filings - ours_filings)[:5]
            )
            before = sum(fact_classes.values())
            reconcile_quarter(
                mine=mine, units=units, theirs=theirs, codebook=codebook,
                classes=fact_classes, examples=fact_examples,
            )
            rows = legacy.execute(
                sa.text(
                    """
                    SELECT symbol, report_category, revenue_acc, op_income_acc,
                           non_op_income_acc, pretax_income_acc,
                           net_income_acc, eps_acc, capital
                      FROM quarterly_reports_xbrl
                     WHERE date = :quarter AND period_type = 'accumulated'
                    """
                ),
                {"quarter": label},
            ).mappings()
            for row in rows:
                reconcile_quarterly_row(
                    row=row, mine=mine, units=units, categories=categories,
                    classes=quarterly_classes, examples=quarterly_examples,
                )
            per_quarter.append(
                {
                    "quarter": label,
                    "our_filings": len(ours_filings),
                    "legacy_filings": len(legacy_filings),
                    "compared_facts": sum(fact_classes.values()) - before,
                }
            )
            print(f"{label} done", file=sys.stderr, flush=True)

    report = {
        "reconciliation": {
            "window": f"{args.start}..{args.end}",
            "coverage": {
                **coverage,
                "only_ours_examples": only_ours_examples[:20],
                "only_legacy_examples": only_legacy_examples[:20],
            },
            "per_quarter": per_quarter,
            "facts": {
                "classes": dict(sorted(fact_classes.items())),
                "examples": dict(fact_examples),
            },
            "quarterly_reports_xbrl": {
                "classes": dict(sorted(quarterly_classes.items())),
                "examples": dict(quarterly_examples),
            },
        }
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    # Explained: identical, the share counts legacy's consumers scale, the
    # rows that belong to filings v1 excludes, and the codes legacy's own
    # codebook filtered out. Everything else is a finding.
    unexplained = sum(
        count
        for name, count in fact_classes.items()
        if name not in {
            "identical",
            "identical:share_count_legacy_consumers_scale",
            "absent_from_ours:filing_outside_v1_scope",
            "absent_from_legacy:outside_legacy_codebook",
        }
    )
    return 0 if unexplained == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
