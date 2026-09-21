#!/usr/bin/env python
"""Step 23-c — check every stored filing's evidence against its archive file.

Versions an official fetch captured first are counted and skipped: they
carry their own `capture_bound`, and they are not the archive's to answer
for.

The acceptance criterion is that a filing the legacy daily job captured
resolves under Market PIT no earlier than that capture, and this re-derives the
claim rather than trusting the import that wrote it: for each stored version it
finds the archive file the request would have read, applies
`archive_capture_bound` to that file's mtime, and compares the answer with what
the database holds.

Three things are checked, per version:

- the evidence type is the one the file's mtime entitles it to;
- a `legacy_capture_bound` instant is exactly the file's mtime;
- a `release_rule` instant is exactly what `financial_statements_general@1`
  resolves for that quarter, and names the rule by version.

Exit code 0 means every version agrees with its own file.

Usage:

    python scripts/verify_financial_filing_evidence.py --database-url …
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

import sqlalchemy as sa

from stock_data_center.evidence import EvidencePolicyService
from stock_data_center.ingestion.adapters.financial_filing_archive import (
    DEFAULT_ARCHIVE_ROOT,
    EVIDENCE_LABEL,
    STATUTORY_RULE,
    archive_capture_bound,
)

QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


def archive_mtime(root: Path, *, code: str, year: int, quarter: int):
    folder = root / str(year) / f"{year}Q{quarter}"
    matches = sorted(folder.glob(f"{year}Q{quarter}_{code}_*.html"))
    if len(matches) != 1:
        return None
    return datetime.fromtimestamp(matches[0].stat().st_mtime, tz=UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verify-financial-filing-evidence")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url is required")

    engine = sa.create_engine(args.database_url)
    counts: Counter = Counter()
    failures: list[dict] = []
    rule_instants: dict[tuple[int, int], datetime] = {}
    rules = EvidencePolicyService()._rules  # noqa: SLF001 - the same resolver

    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                """
                SELECT s.security_code, v.report_year, v.report_quarter, v.id,
                       e.evidence_type, e.published_at, e.evidence_source,
                       EXISTS (
                           SELECT 1 FROM publication_evidence c
                            WHERE c.financial_filing_version_id = v.id
                              AND c.evidence_type = 'capture_bound'
                       ) AS officially_captured
                  FROM financial_filing_versions v
                  JOIN security s ON s.id = v.security_id
                  LEFT JOIN publication_evidence e
                         ON e.financial_filing_version_id = v.id
                        AND e.evidence_type IN
                            ('legacy_capture_bound', 'release_rule')
                 WHERE v.source = 'mops_t164sb01'
                 ORDER BY v.id
                """
            )
        ).mappings()
        for row in rows:
            if row["officially_captured"]:
                # The archive adapter files under the official source code on
                # purpose (CLAUDE.md §30), so this query sees official fetches
                # too. A version an official `first_capture` saw first carries
                # its own `capture_bound`, which outranks anything the archive
                # could claim — judging it against an archive file would report
                # a failure where the evidence is right.
                counts["officially_captured"] += 1
                continue
            code = row["security_code"]
            year, quarter = row["report_year"], row["report_quarter"]
            mtime = archive_mtime(
                args.archive_root, code=code, year=year, quarter=quarter
            )
            if mtime is None:
                counts["no_archive_file"] += 1
                continue
            expected_bound = archive_capture_bound(
                report_year=year, report_quarter=quarter, mtime=mtime
            )
            expected_type = (
                "legacy_capture_bound" if expected_bound else "release_rule"
            )
            problem = None
            if row["evidence_type"] != expected_type:
                problem = f"type {row['evidence_type']} != {expected_type}"
            elif expected_bound is not None:
                if row["published_at"] != expected_bound:
                    problem = f"{row['published_at']} != mtime {expected_bound}"
                elif row["evidence_source"] != (
                    f"{EVIDENCE_LABEL} {expected_bound.date().isoformat()}"
                ):
                    problem = f"source {row['evidence_source']}"
            else:
                key = (year, quarter)
                if key not in rule_instants:
                    month, day = QUARTER_END[quarter]
                    rule_instants[key] = rules.instant_for(
                        connection,
                        rule_id=STATUTORY_RULE[0],
                        version=STATUTORY_RULE[1],
                        period=date(year, month, day),
                    )
                if row["published_at"] != rule_instants[key]:
                    problem = f"{row['published_at']} != rule {rule_instants[key]}"
                elif row["evidence_source"] != (
                    f"{STATUTORY_RULE[0]}@{STATUTORY_RULE[1]}"
                ):
                    problem = f"source {row['evidence_source']}"
            if problem is None:
                counts[expected_type] += 1
            else:
                counts["disagrees"] += 1
                if len(failures) < 20:
                    failures.append(
                        {"security_code": code, "period": f"{year}Q{quarter}",
                         "problem": problem}
                    )

    report = {
        "verification": {
            "archive_root": str(args.archive_root),
            "counts": dict(sorted(counts.items())),
            "failures": failures,
            "passed": counts["disagrees"] == 0 and counts["no_archive_file"] == 0,
        }
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["verification"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
