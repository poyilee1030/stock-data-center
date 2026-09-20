#!/usr/bin/env python
"""Parse the legacy iXBRL archive and report what the source actually contains.

Step 23-a's evidence tool. The unit tests run on excerpts; this runs the same
parser over real documents so the claims in `docs/source_field_audit.md` §4.8 —
which header values exist, which industries and markets, which units, scales and
context shapes, and how many facts a quarter really holds — are measured rather
than assumed. It writes nothing to the database and fetches nothing.

    python scripts/scan_xbrl_archive.py --workers 8
    python scripts/scan_xbrl_archive.py --sample-per-quarter 20 --json out.json

Every document that fails to parse is reported with its error; the scan does not
stop, because the point is to see the whole distribution of defects at once.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_data_center.financials.classification import SourcePeriodRole  # noqa: E402
from stock_data_center.financials.ixbrl import (  # noqa: E402
    IXBRLParseError,
    classify_context_role,
    parse_ixbrl_report,
)

DEFAULT_ARCHIVE_ROOT = Path.home() / "GitHubLL/my_stock_project/data/raw/xbrl"
FILE_NAME = re.compile(r"^(?P<year>\d{4})Q(?P<quarter>\d)_(?P<symbol>[0-9A-Z]+)_(?P<run>\d{8})\.html$")


@dataclass(frozen=True, slots=True)
class DocumentScan:
    path: str
    year: int
    quarter: int
    symbol: str
    run_date: str
    error: str | None = None
    report_type: str | None = None
    report_category: str | None = None
    market: str | None = None
    industry_sector: str | None = None
    schema_ref: str | None = None
    fact_count: int = 0
    context_count: int = 0
    note_block_count: int = 0
    placeholder_facts: int = 0
    malformed_numeric_facts: int = 0
    units: tuple[str, ...] = ()
    scales: tuple[int, ...] = ()
    prefix_case_repairs: tuple[str, ...] = ()
    header_matches_file_name: bool = False
    current_roles: tuple[str, ...] = ()


def scan_document(path: Path) -> DocumentScan:
    name = FILE_NAME.match(path.name)
    if name is None:
        return DocumentScan(str(path), 0, 0, "", "", error=f"unexpected file name: {path.name}")
    year, quarter = int(name["year"]), int(name["quarter"])
    base = dict(
        path=str(path), year=year, quarter=quarter, symbol=name["symbol"], run_date=name["run"]
    )
    try:
        report = parse_ixbrl_report(path.read_bytes())
    except IXBRLParseError as error:
        return DocumentScan(**base, error=str(error))
    except Exception as error:  # the scan is here to find these
        return DocumentScan(**base, error=f"{type(error).__name__}: {error}")

    roles = {
        classify_context_role(report, ref).period_role
        for ref, context in report.contexts.items()
        if context.period_type == "duration"
    }
    return DocumentScan(
        **base,
        report_type=report.header.report_type.value,
        report_category=report.header.report_category.value,
        market=report.header.market.value,
        industry_sector=report.header.industry_sector.value,
        schema_ref=report.header.taxonomy_schema_ref,
        fact_count=len(report.facts),
        context_count=len(report.contexts),
        note_block_count=report.note_block_count,
        placeholder_facts=sum(1 for fact in report.facts if fact.is_placeholder),
        malformed_numeric_facts=report.malformed_numeric_facts,
        units=tuple(sorted(set(report.units.values()))),
        scales=tuple(sorted({fact.scale for fact in report.facts})),
        prefix_case_repairs=report.prefix_case_repairs,
        header_matches_file_name=(
            report.header.company_id == name["symbol"]
            and report.header.report_year == year
            and report.header.report_quarter == quarter
        ),
        current_roles=tuple(sorted(role.value for role in roles if role is not SourcePeriodRole.OTHER)),
    )


def documents(root: Path, sample_per_quarter: int | None) -> list[Path]:
    by_quarter: dict[tuple[str, str], list[Path]] = {}
    for path in sorted(root.glob("20*/*/*.html")):
        by_quarter.setdefault((path.parent.parent.name, path.parent.name), []).append(path)
    if sample_per_quarter is None:
        return [path for paths in by_quarter.values() for path in paths]
    picked: list[Path] = []
    for paths in by_quarter.values():
        step = max(1, len(paths) // sample_per_quarter)
        picked.extend(paths[::step][:sample_per_quarter])
    return picked


def report(scans: list[DocumentScan]) -> None:
    failures = [scan for scan in scans if scan.error]
    parsed = [scan for scan in scans if not scan.error]
    print(f"documents          {len(scans):,}")
    print(f"parsed             {len(parsed):,}")
    print(f"failed             {len(failures):,}")
    for scan in failures[:20]:
        print(f"  {Path(scan.path).name}: {scan.error}")

    for field in ("report_type", "report_category", "market", "industry_sector"):
        counts = Counter(getattr(scan, field) for scan in parsed)
        print(f"\n{field}")
        for value, count in counts.most_common():
            print(f"  {count:>7,}  {value}")

    print("\nunits")
    for value, count in Counter(unit for scan in parsed for unit in scan.units).most_common():
        print(f"  {count:>7,}  {value}")
    print("\nscales")
    for value, count in Counter(scale for scan in parsed for scale in scan.scales).most_common():
        print(f"  {count:>7,}  {value}")
    print("\ntaxonomy schemaRef")
    for value, count in Counter(scan.schema_ref for scan in parsed).most_common(20):
        print(f"  {count:>7,}  {value}")
    print("\ncurrent EPS period roles present per document")
    for value, count in Counter(scan.current_roles for scan in parsed).most_common():
        print(f"  {count:>7,}  {', '.join(value) or '(none)'}")

    repairs = Counter(
        repair for scan in parsed for repair in scan.prefix_case_repairs
    )
    print(f"\nprefix case repairs {sum(repairs.values()):,}")
    for value, count in repairs.most_common():
        print(f"  {count:>7,}  {value}")

    mismatched = [scan for scan in parsed if not scan.header_matches_file_name]
    print(f"\nheader disagrees with file name  {len(mismatched):,}")
    for scan in mismatched[:20]:
        print(f"  {Path(scan.path).name}")

    placeholders = sum(scan.placeholder_facts for scan in parsed)
    malformed = sum(scan.malformed_numeric_facts for scan in parsed)
    print(f"\nplaceholder facts  {placeholders:,} "
          f"in {sum(1 for s in parsed if s.placeholder_facts):,} documents")
    print(f"prose in nonFraction {malformed:,} "
          f"in {sum(1 for s in parsed if s.malformed_numeric_facts):,} documents")

    facts = sum(scan.fact_count for scan in parsed)
    notes = sum(scan.note_block_count for scan in parsed)
    print(f"\nnumeric facts      {facts:,}")
    print(f"narrative blocks   {notes:,}")
    if parsed:
        print(f"facts per document min {min(s.fact_count for s in parsed):,} "
              f"max {max(s.fact_count for s in parsed):,} "
              f"mean {facts // len(parsed):,}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--sample-per-quarter", type=int, default=None)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    if not args.archive_root.is_dir():
        print(f"archive root not found: {args.archive_root}", file=sys.stderr)
        return 2

    paths = documents(args.archive_root, args.sample_per_quarter)
    print(f"scanning {len(paths):,} documents under {args.archive_root} "
          f"with {args.workers} workers", flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        scans = list(pool.map(scan_document, paths, chunksize=8))

    report(scans)
    if args.json:
        args.json.write_text(
            json.dumps([asdict(scan) for scan in scans], ensure_ascii=False)
        )
        print(f"\nwrote {args.json}")
    return 1 if any(scan.error for scan in scans) else 0


if __name__ == "__main__":
    raise SystemExit(main())
