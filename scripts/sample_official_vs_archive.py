#!/usr/bin/env python
"""Step 23-c — the archive-vs-official sample gate.

ROADMAP §23 accepts the legacy archive as the source of 2020Q1–2026Q2 instead
of 45,000 official requests, on one condition: a sample proves the archived
documents are the documents MOPS serves today. At the 3-second pace MOPS has
enforced since it blocked the legacy scraper on 2026-07-02, a full re-fetch is
about 38 hours and another blocking risk; a stratified sample is minutes.

What a difference means matters more than the count, so each sampled document
lands in one of four buckets:

- `identical` — the official cp950 response decodes to the archived UTF-8 copy
  character for character. Nothing was lost in re-encoding and nothing has
  changed since.
- `same_filing` — the two documents differ somewhere, but their statement rows
  fingerprint the same under `mops-filing-revision:v1`, so they are one source
  revision and the archive's business content is today's. Whitespace and the
  parts of the page outside the three statements are not v1 data.
- `corrected_since_capture` — the statement rows differ, so MOPS has served a
  corrected filing since the archive was written. This is a real later
  revision, not archive damage: importing the archived copy stores what was
  filed then, and Step 27's forward capture stores the correction as its own
  version when it fetches it.
- `no_longer_served` — MOPS answers `檔案不存在!` for both REPORT_IDs. The
  document existed when the archive was written and the endpoint no longer
  offers it, which is the archive doing the job it is kept for.

Only a document that cannot be explained at all counts against the gate. The
threshold is on `unexplained`, which is a document whose official response is
unreadable or answers with a different filer, because that is what would mean
the archive and the endpoint disagree about identity.

Usage:

    python scripts/sample_official_vs_archive.py --per-quarter 10 --seed 23
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from stock_data_center.financials.ixbrl import decode_ixbrl_document
from stock_data_center.ingestion.adapters.financial_filing import (
    MOPSFinancialFilingAdapter,
    REPORT_IDS,
)
from stock_data_center.ingestion.adapters.financial_filing_archive import (
    DEFAULT_ARCHIVE_ROOT,
    LegacyFinancialFilingArchiveAdapter,
)
from stock_data_center.ingestion.http import HttpSourceFetcher, RetryingFetcher
from stock_data_center.ingestion.models import (
    FinancialFilingArchiveRequest,
    FinancialFilingRequest,
    SourceDataError,
)

QUARTERS = [
    (year, quarter)
    for year in range(2020, 2027)
    for quarter in (1, 2, 3, 4)
    if (year, quarter) <= (2026, 2)
]


@dataclass(frozen=True, slots=True)
class SampleResult:
    period: str
    security_code: str
    report_id: str | None
    verdict: str
    detail: str | None = None


def archived_documents(root: Path, year: int, quarter: int) -> list[str]:
    folder = root / str(year) / f"{year}Q{quarter}"
    if not folder.is_dir():
        raise FileNotFoundError(f"no archived quarter at {folder}")
    return sorted({path.stem.split("_")[1] for path in folder.glob("*.html")})


def compare(
    *,
    archive_adapter: LegacyFinancialFilingArchiveAdapter,
    official_adapter: MOPSFinancialFilingAdapter,
    fetcher,
    root: Path,
    year: int,
    quarter: int,
    code: str,
) -> SampleResult:
    period = f"{year}Q{quarter}"
    archive_request = FinancialFilingArchiveRequest(code, year, quarter)
    pattern = Path(archive_adapter.resource(archive_request).source_uri)
    matches = sorted(pattern.parent.glob(pattern.name))
    archived_bytes = matches[0].read_bytes()
    try:
        archived = archive_adapter.parse(archived_bytes, archive_request)
    except SourceDataError as error:
        # Excluded from v1 at the boundary — a financial-industry issuer or a
        # filer outside 上市/上櫃 — so the endpoint is not asked about it.
        return SampleResult(period, code, None, "out_of_scope", error.reason_code)

    report_id = REPORT_IDS[archived.report_category]
    official_request = FinancialFilingRequest(code, year, quarter, report_id)
    try:
        fetched = fetcher.fetch(official_adapter.resource(official_request))
    except Exception as error:  # noqa: BLE001 - reported, not swallowed
        return SampleResult(
            period, code, report_id, "unexplained", f"fetch failed: {error}"
        )

    try:
        official = official_adapter.parse(fetched.content, official_request)
    except SourceDataError as error:
        if error.reason_code == "no_such_report":
            return SampleResult(period, code, report_id, "no_longer_served", None)
        return SampleResult(period, code, report_id, "unexplained", error.reason_code)

    if decode_ixbrl_document(fetched.content) == decode_ixbrl_document(
        archived_bytes
    ):
        return SampleResult(period, code, report_id, "identical", None)
    if official.filing_key == archived.filing_key:
        return SampleResult(period, code, report_id, "same_filing", None)
    return SampleResult(
        period,
        code,
        report_id,
        "corrected_since_capture",
        f"{archived.filing_key} -> {official.filing_key}",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sample-official-vs-archive")
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument(
        "--per-quarter", type=int, default=10,
        help="documents sampled from each of the 26 quarters",
    )
    parser.add_argument(
        "--seed", type=int, default=23,
        help="the sample is seeded so the same command draws the same "
        "documents and the recorded rate can be reproduced",
    )
    parser.add_argument(
        "--max-unexplained-rate", type=float, default=0.01,
        help="fail above this share of documents the endpoint could not "
        "explain; ROADMAP §23 makes the whole archive import fall back to a "
        "full re-fetch when the gate fails",
    )
    args = parser.parse_args(argv)

    rng = random.Random(args.seed)
    archive_adapter = LegacyFinancialFilingArchiveAdapter(
        archive_root=args.archive_root
    )
    official_adapter = MOPSFinancialFilingAdapter()
    # The per-host governor paces MOPS at 3 s whatever this asks for.
    fetcher = RetryingFetcher(HttpSourceFetcher())

    results: list[SampleResult] = []
    for year, quarter in QUARTERS:
        codes = archived_documents(args.archive_root, year, quarter)
        for code in rng.sample(codes, min(args.per_quarter, len(codes))):
            result = compare(
                archive_adapter=archive_adapter,
                official_adapter=official_adapter,
                fetcher=fetcher,
                root=args.archive_root,
                year=year,
                quarter=quarter,
                code=code,
            )
            results.append(result)
            print(
                f"{result.period} {result.security_code} {result.verdict}"
                + (f" {result.detail}" if result.detail else ""),
                file=sys.stderr,
                flush=True,
            )

    counts: dict[str, int] = {}
    for result in results:
        counts[result.verdict] = counts.get(result.verdict, 0) + 1
    compared = sum(
        count for verdict, count in counts.items() if verdict != "out_of_scope"
    )
    unexplained = counts.get("unexplained", 0)
    rate = unexplained / compared if compared else 0.0
    report = {
        "sample_gate": {
            "run_at": datetime.now(UTC).isoformat(),
            "archive_root": str(args.archive_root),
            "seed": args.seed,
            "per_quarter": args.per_quarter,
            "quarters": len(QUARTERS),
            "sampled": len(results),
            "compared": compared,
            "verdicts": dict(sorted(counts.items())),
            "unexplained_rate": round(rate, 6),
            "max_unexplained_rate": args.max_unexplained_rate,
            "passed": rate <= args.max_unexplained_rate,
            "results": [asdict(result) for result in results],
        }
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if rate <= args.max_unexplained_rate else 1


if __name__ == "__main__":
    raise SystemExit(main())
