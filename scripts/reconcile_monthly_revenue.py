#!/usr/bin/env python
"""Reconcile the imported monthly revenue against legacy `stock_db`.

Step 22-b, the shape of the earlier reconciliations: the import counts from
the per-page manifests, the Step 16 coverage report over calendar months, the
page scan, and the row-level comparison with every difference classified
(CLAUDE.md §78).

Three things this dataset needs that a daily one does not:

- **The page scan.** A market-month is two pages, `_0` domestic and `_1`
  foreign/KY, and the same company must never be on both: one source would
  then hold two versions of one logical key for one month and they would
  alternate (CLAUDE.md §30). Every stored month's pages are re-parsed from
  their raw artifacts — read through the store, which checks size and SHA-256
  first — and intersected. A month is complete when each of its two pages is
  either stored or quarantined as 查無資料; a month with no foreign issuer is
  not the same as a page nobody asked for.
- **KY is new coverage, not a difference.** The legacy scraper hard-coded the
  `_0` URL (`scraper/monthly/fetch_monthly_revenue.py`), so legacy holds no
  foreign issuer at all. A row we hold and legacy does not is
  `absent_from_legacy:foreign_page` when the `_1` page of that month is where
  we read it, which the page scan proves.
- **The source rewrites its own history by issuer status.** `t21sc03` is
  regenerated: a month's page lists the issuers that hold that status *now*,
  not the ones that held it then. An issuer that has since left both markets
  is absent from the 2020 page as well, though legacy recorded it in 2020 and
  its prices are in this database. Three of them are the permanent fixture
  (audit §4.7, Step 22-b findings): 2867 三商壽 and 6806 森崴能源 are on today's `pub` page for
  2020M01, and 3454 晶睿 is on none of the four. `pub` and `rotc` are outside
  the v1 universe by owner decision, so those rows are not recoverable and
  not a gap in what we do cover.

A comparative is compared here, never reconciled: the published values are
stored exactly as published (CLAUDE.md §54). A difference against legacy's
copy is the page having changed between legacy's capture and ours, and the
proof offered is the source's own later page.

Proof for a value difference, in the order tried:

- `note_differs:legacy_collapsed_whitespace` — the two notes are the same text
  once runs of whitespace collapse. Legacy's CSV pipeline did that; the page
  says what we stored.
- `note_differs:legacy_decoded_the_same_bytes_differently` — both notes encode
  back to the same cp950 bytes. `‧` (U+2027) and `•` (U+2022) are both
  `A1 45` in the page; legacy's table chose the other one.
- `note_differs:legacy_lost_bytes_decoding` — legacy's note carries U+FFFD
  where ours has a character the page encodes (不銹鋼's 銹 is `F9 D7`, the
  byte strict big5 rejects and cp950 reads).
- `value_differs:legacy_capture_stale` — the next month's page publishes this
  issuer's 上月營收 and it equals ours, not legacy's. The source's own later
  page carries our value. One month past `--end` is loaded for this lookup, so
  the window's last month can be proven like any other.
- `value_differs:restated_by_a_correction` — a comparative whose inputs include
  a month proven stale above. A corrected 當月營收 restates that row's own
  percentages and note, the next month's 上月營收 and 上月比較增減, the year's
  cumulative figures, and next year's 去年 columns. Each field is checked
  against its own input months, not waved through by being on the same row.
- `value_differs:legacy_disagrees_with_the_month_it_restates` — a comparative
  restating another month, where ours equals what we hold for that month and
  legacy's does not.
- `value_differs:legacy_row_internally_inconsistent` — a cumulative figure that
  does not follow from legacy's own neighbouring rows, where ours does (1611
  2026M05: legacy holds 341,881 where its own 338,195 + 86,945 is our 425,140).
- `value_differs:follows_the_amounts_it_is_computed_from` — a published
  percentage whose two amounts differ in the same row and are themselves
  explained.
- `note_differs:legacy_read_NA_as_missing` — the page's note is the literal
  `NA` and legacy holds NULL. All six of them are that (1531 2022M02, 2337
  five months).
- `value_differs:known_late_republication` — one of the sixteen rows named in
  `KNOWN_LATE_REPUBLICATION`, where the issuer republished its 備註 or its 去年
  columns after legacy read the page and left 當月營收 alone. Nothing in either
  database says what the page carried on legacy's day, so each row is named
  rather than covered by a rule.
- `value_differs:unexplained:<field>` — everything else, listed in full. These
  do not pass.

Usage:

    python scripts/reconcile_monthly_revenue.py \
        --database-url postgresql+psycopg://... \
        --legacy-database-url postgresql+psycopg://user:password@127.0.0.1:5419/stock_db
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stock_data_center.coverage import CoverageValidator
from stock_data_center.ingestion.adapters import (
    MOPSOtcMonthlyRevenueAdapter,
    MOPSSiiMonthlyRevenueAdapter,
)
from stock_data_center.ingestion.models import MonthlyRevenueRequest, RevenuePage
from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
from stock_data_center.monthly_revenue.models import RevenuePeriod

DATASET = "monthly_revenue"
# source -> (coverage market, legacy market, adapter, the other market's source)
MARKETS = {
    "mops_t21sc03_sii": (
        "TWSE", "SII", MOPSSiiMonthlyRevenueAdapter, "mops_t21sc03_otc",
    ),
    "mops_t21sc03_otc": (
        "TPEx", "OTC", MOPSOtcMonthlyRevenueAdapter, "mops_t21sc03_sii",
    ),
}
# our column -> (legacy column, whether legacy states it in 千元)
LEGACY = {
    "revenue": ("revenue_current", True),
    "revenue_last_month": ("revenue_last_month", True),
    "revenue_last_year_month": ("revenue_last_year", True),
    "cumulative_revenue": ("revenue_cumulative", True),
    "cumulative_revenue_last_year": ("revenue_cumulative_last_year", True),
    "mom_pct": ("mom_pct", False),
    "yoy_pct": ("yoy_pct", False),
    "cumulative_yoy_pct": ("cumulative_yoy_pct", False),
    "note": ("comment", False),
}
FIELDS = tuple(LEGACY)
THOUSAND = Decimal(1000)
WHITESPACE = re.compile(r"\s+")


def encoded(text: str) -> bytes | None:
    """The page bytes this text would have been read from, if it can be."""
    try:
        return text.encode("cp950")
    except UnicodeEncodeError:
        return None


def previous(key: tuple[int, int]) -> tuple[int, int]:
    year, month = key
    return (year - 1, 12) if month == 1 else (year, month - 1)


def following(key: tuple[int, int]) -> tuple[int, int]:
    year, month = key
    return (year + 1, 1) if month == 12 else (year, month + 1)


def year_to_date(key: tuple[int, int], *, offset: int = 0) -> list[tuple[int, int]]:
    year, month = key
    return [(year + offset, index) for index in range(1, month + 1)]


# Which months a published comparative restates. A correction to any of them
# changes the comparative without changing this row's own 當月營收.
INPUT_MONTHS = {
    "revenue_last_month": lambda key: [previous(key)],
    "mom_pct": lambda key: [previous(key)],
    "revenue_last_year_month": lambda key: [(key[0] - 1, key[1])],
    "yoy_pct": lambda key: [(key[0] - 1, key[1])],
    "cumulative_revenue": lambda key: year_to_date(key),
    "cumulative_revenue_last_year": lambda key: year_to_date(key, offset=-1),
    "cumulative_yoy_pct": lambda key: year_to_date(key) + year_to_date(key, offset=-1),
    "note": lambda key: [],
}

# A published percentage and the two published amounts it is computed from.
PERCENT_INPUTS = {
    "mom_pct": ("revenue", "revenue_last_month"),
    "yoy_pct": ("revenue", "revenue_last_year_month"),
    "cumulative_yoy_pct": ("cumulative_revenue", "cumulative_revenue_last_year"),
}
# The rows where the issuer republished a field after legacy read the page,
# leaving 當月營收 alone: nothing in either database proves which text or which
# prior-year figure the page carried on the day legacy read it, so each is
# named here rather than waved through by a rule. All of them are 2026, the
# tail legacy captured most recently.
KNOWN_LATE_REPUBLICATION = {
    # 2425 rewrote its 備註 from 2026M01, adding the 停業單位 figures.
    ("mops_t21sc03_sii", "2425", "2026-01"): {"note"},
    ("mops_t21sc03_sii", "2425", "2026-02"): {"note"},
    ("mops_t21sc03_sii", "2425", "2026-03"): {"note"},
    ("mops_t21sc03_sii", "2425", "2026-04"): {"note"},
    ("mops_t21sc03_sii", "2425", "2026-05"): {"note"},
    # 1235 restated the figure inside its own 備註 (39,332 -> 31,532 仟元).
    ("mops_t21sc03_sii", "1235", "2026-04"): {"note"},
    # 2887 台新新光金: the 去年 columns restated after the merger.
    ("mops_t21sc03_sii", "2887", "2026-04"): {
        "revenue_last_year_month", "cumulative_revenue_last_year",
        "yoy_pct", "cumulative_yoy_pct",
    },
    # 2608 restated its 去年 columns; 當月營收 and the cumulative are unchanged.
    ("mops_t21sc03_sii", "2608", "2026-08"): {
        "revenue_last_year_month", "cumulative_revenue_last_year", "yoy_pct",
    },
    # 6692 corrected 增加 to 減少 in its own 備註.
    ("mops_t21sc03_otc", "6692", "2026-05"): {"note"},
    # 4402 and 6101 replaced their 備註 with a longer explanation.
    ("mops_t21sc03_otc", "4402", "2026-06"): {"note"},
    ("mops_t21sc03_otc", "6101", "2026-07"): {"note"},
}

# A comparative that restates another month, and which month it restates.
RESTATES = {
    "revenue_last_month": (lambda key: previous(key), "revenue"),
    "revenue_last_year_month": (lambda key: (key[0] - 1, key[1]), "revenue"),
    "cumulative_revenue_last_year": (
        lambda key: (key[0] - 1, key[1]), "cumulative_revenue",
    ),
}


def rolls_forward(rows_by_month, code, key) -> bool | None:
    """Whether a cumulative figure follows from the same series' own rows.

    January's cumulative is its own revenue; any other month's is the previous
    month's cumulative plus this month's revenue. `None` when the rows needed
    are not there to say either way.
    """
    row = rows_by_month.get(key, {}).get(code)
    if row is None or row["cumulative_revenue"] is None or row["revenue"] is None:
        return None
    total = Decimal(str(row["cumulative_revenue"]))
    revenue = Decimal(str(row["revenue"]))
    if key[1] == 1:
        return total == revenue
    earlier = rows_by_month.get(previous(key), {}).get(code)
    if earlier is None or earlier["cumulative_revenue"] is None:
        return None
    return total == Decimal(str(earlier["cumulative_revenue"])) + revenue


def consistent_cumulative(code, key, ours, theirs) -> bool:
    """Ours follows its own series and legacy's does not."""
    return rolls_forward(ours, code, key) is True and (
        rolls_forward(theirs, code, key) is False
    )


def month_end(key: tuple[int, int]) -> date:
    year, month = key
    return date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)


# The classes a proof stands behind. Anything else fails the run.
EXPLAINED = frozenset({
    "value_differs:known_late_republication",
    "value_differs:legacy_disagrees_with_the_month_it_restates",
    "value_differs:legacy_row_internally_inconsistent",
    "value_differs:follows_the_amounts_it_is_computed_from",
    "note_differs:legacy_read_NA_as_missing",
    "value_differs:restated_by_a_correction",
    "absent_from_legacy:legacy_missed_that_month",
    "note_differs:legacy_collapsed_whitespace",
    "note_differs:legacy_decoded_the_same_bytes_differently",
    "note_differs:legacy_lost_bytes_decoding",
    "absent_from_legacy:issuer_listed_after_that_month",
    "value_differs:legacy_capture_stale",
    "absent_from_source:issuer_on_no_page_of_either_market",
    "absent_from_source:stored_under_the_other_market",
    "absent_from_legacy:foreign_page",
    "absent_from_legacy:legacy_filed_it_under_the_other_market",
})


def periods(start: RevenuePeriod, end: RevenuePeriod) -> list[RevenuePeriod]:
    out, year, month = [], start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append(RevenuePeriod(year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def import_counts(connection, source: str, months: list[str]) -> dict:
    rows = connection.execute(
        sa.text(
            """
            SELECT status, result_counts, reconciliation, source_scope
              FROM import_manifests
             WHERE dataset_code = :dataset AND source = :source
               AND source_scope ->> 'period' = ANY(:months)
            """
        ),
        {"dataset": DATASET, "source": source, "months": months},
    ).mappings()
    totals: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    pages: Counter[str] = Counter()
    versions: set[str] = set()
    variants: Counter[str] = Counter()
    units: set[str] = set()
    for row in rows:
        statuses[row["status"]] += 1
        pages[row["source_scope"].get("page", "?")] += 1
        for key, value in (row["result_counts"] or {}).items():
            if isinstance(value, int):
                totals[key] += value
        reconciliation = row["reconciliation"] or {}
        if reconciliation.get("adapter_version"):
            versions.add(reconciliation["adapter_version"])
        if reconciliation.get("header_variant"):
            variants[reconciliation["header_variant"]] += 1
        if reconciliation.get("amount_unit"):
            units.add(reconciliation["amount_unit"])
    return {
        "manifest_statuses": dict(statuses),
        "manifests_per_page": dict(sorted(pages.items())),
        "adapter_versions": sorted(versions),
        "header_variants": dict(variants),
        "amount_units": sorted(units),
        **{key: totals[key] for key in (
            "raw_artifact_count", "business_version_count", "dedup_count",
            "evidence_count", "unknown_publication_count",
            "rejected_quarantined_count",
        )},
    }


def quarantines(connection, source: str, months: list[str]) -> list[dict]:
    rows = connection.execute(
        sa.text(
            """
            SELECT c.resource_key, c.error_code, c.error_detail
              FROM import_checkpoints c
              JOIN import_manifests m ON m.import_id = c.import_id
             WHERE m.dataset_code = :dataset AND m.source = :source
               AND c.status = 'quarantined'
             ORDER BY c.resource_key
            """
        ),
        {"dataset": DATASET, "source": source},
    ).mappings()
    return [
        dict(row) for row in rows
        if row["resource_key"].split(":")[2] in months
    ]


def stored(connection, source: str, start: date, end: date) -> dict:
    """Latest revision per (security, month), chosen deterministically."""
    rows = connection.execute(
        sa.text(
            f"""
            SELECT DISTINCT ON (v.security_id, v.revenue_year, v.revenue_month)
                   s.security_code, v.revenue_year, v.revenue_month,
                   {", ".join(f"v.{name}" for name in FIELDS)}
              FROM monthly_revenue_versions v
              JOIN security s ON s.id = v.security_id
             WHERE v.source = :source AND v.revenue_period BETWEEN :start AND :end
             ORDER BY v.security_id, v.revenue_year, v.revenue_month,
                      v.ingested_at DESC, v.id DESC
            """
        ),
        {"source": source, "start": start, "end": end},
    ).mappings()
    grouped: dict[tuple[int, int], dict[str, dict]] = {}
    for row in rows:
        grouped.setdefault((row["revenue_year"], row["revenue_month"]), {})[
            row["security_code"]
        ] = {name: row[name] for name in FIELDS}
    return grouped


def legacy_rows(connection, market: str, months: list[str]) -> dict:
    rows = connection.execute(
        sa.text(
            f"""
            SELECT date, symbol,
                   {", ".join(column for column, _ in LEGACY.values())}
              FROM monthly_revenue
             WHERE market = :market AND date = ANY(:months)
            """
        ),
        {"market": market, "months": months},
    ).mappings()
    grouped: dict[tuple[int, int], dict[str, dict]] = {}
    for row in rows:
        year, month = int(row["date"][:4]), int(row["date"][5:])
        grouped.setdefault((year, month), {})[row["symbol"]] = {
            name: row[column] for name, (column, _) in LEGACY.items()
        }
    return grouped


def first_priced(connection) -> dict[str, date]:
    """The first date each security was priced, where this database holds it.

    Used only as reconciliation evidence, for an issuer legacy never saw: if
    it was first priced after the month in question, it was not a listed
    security then and the regenerated page is filing its pre-listing revenue.
    """
    rows = connection.execute(
        sa.text(
            """
            SELECT s.security_code, min(v.trade_date)
              FROM daily_price_versions v
              JOIN security s ON s.id = v.security_id
             GROUP BY s.security_code
            """
        )
    ).all()
    return dict(rows)


def as_decimal(value, *, thousand: bool) -> Decimal | None:
    if value is None or isinstance(value, str):
        return value
    number = Decimal(str(value))
    return number * THOUSAND if thousand else number


def differs(ours, theirs, *, thousand: bool) -> bool:
    if ours is None or theirs is None or isinstance(ours, str) or isinstance(theirs, str):
        return ours != theirs
    return Decimal(ours) != as_decimal(theirs, thousand=thousand)


def page_scan(connection, source: str, adapter_class, months: list[str],
              raw_root: Path) -> dict:
    """Every month's pages, re-parsed from raw artifacts this verifies first.

    A month is complete when each page is either stored or answered 查無資料:
    the source publishes no `_1` page for a month with no foreign issuer, and
    that is not the same thing as a page nobody ever asked for. The two are
    told apart by the quarantine's own reason code, not by absence.
    """
    rows = connection.execute(
        sa.text(
            """
            SELECT DISTINCT ON (c.resource_key)
                   c.resource_key, c.status, c.error_code,
                   r.storage_uri, r.raw_artifact_hash, r.byte_size
              FROM import_checkpoints c
              JOIN import_manifests m ON m.import_id = c.import_id
              JOIN raw_artifacts r ON r.id = c.last_raw_artifact_id
             WHERE m.dataset_code = :dataset AND m.source = :source
               AND c.status IN ('succeeded', 'quarantined')
             ORDER BY c.resource_key, c.updated_at DESC
            """
        ),
        {"dataset": DATASET, "source": source},
    ).all()
    adapter = adapter_class()
    # Verified, as `reconcile_institutional_investors.py` reads them: the
    # §30 evidence this scan produces must not rest on unchecked bytes.
    store = LocalRawArtifactStore(raw_root)
    by_month: dict[str, dict[str, set[str]]] = {}
    unpublished: dict[str, list[str]] = {}
    for resource_key, status, error_code, storage_uri, digest, size in rows:
        _, _, period_text, page_value = resource_key.split(":")
        if period_text not in months:
            continue
        if status == "quarantined":
            if error_code == "no_data_for_period":
                unpublished.setdefault(period_text, []).append(page_value)
            continue
        year, month = (int(part) for part in period_text.split("-"))
        parsed = adapter.parse(
            store.read(
                storage_uri=storage_uri,
                expected_digest=digest,
                expected_byte_size=size,
            ),
            MonthlyRevenueRequest(
                RevenuePeriod(year, month), RevenuePage(page_value)
            ),
        )
        by_month.setdefault(period_text, {})[page_value] = {
            row.security_code for row in parsed.rows
        }
    overlaps = {
        month: sorted(pages["0"] & pages["1"])
        for month, pages in by_month.items()
        if len(pages) == 2 and pages["0"] & pages["1"]
    }
    missing = {
        month: sorted(
            page for page in ("0", "1")
            if page not in by_month.get(month, {})
            and page not in unpublished.get(month, ())
        )
        for month in months
    }
    return {
        "months_scanned": len(by_month),
        "months_with_both_pages": sum(1 for p in by_month.values() if len(p) == 2),
        "months_the_source_publishes_no_page_for": {
            month: sorted(pages) for month, pages in sorted(unpublished.items())
        },
        "months_missing_a_page": {
            month: pages for month, pages in sorted(missing.items()) if pages
        },
        "domestic_rows": sum(len(p.get("0", ())) for p in by_month.values()),
        "foreign_rows": sum(len(p.get("1", ())) for p in by_month.values()),
        "months_with_a_company_on_both_pages": overlaps,
        "by_month": by_month,
    }


def compare(connection, legacy_connection, source: str, start: RevenuePeriod,
            end: RevenuePeriod, scans: dict, ours_by_source: dict,
            priced: dict[str, date]) -> dict:
    market, legacy_market, _, other_source = MARKETS[source]
    month_keys = periods(start, end)
    legacy_months = [f"{p.year}M{p.month:02d}" for p in month_keys]
    other_legacy_market = "OTC" if legacy_market == "SII" else "SII"
    ours = ours_by_source[source]
    other_ours = ours_by_source[other_source]
    theirs = legacy_rows(legacy_connection, legacy_market, legacy_months)
    other_theirs = legacy_rows(legacy_connection, other_legacy_market, legacy_months)
    foreign = {
        (int(month[:4]), int(month[5:])): pages.get("1", set())
        for month, pages in scans[source]["by_month"].items()
    }

    differences: Counter[str] = Counter()
    differing_fields: Counter[str] = Counter()
    examples: dict[str, list[dict]] = {}
    compared = 0
    # Counted and searched over the window only: the extra month is there to
    # be looked up, not to be reported on.
    window_keys = {(p.year, p.month) for p in month_keys}
    ever_in_ours = {
        code for key, rows in ours.items() if key in window_keys for code in rows
    }
    ever_in_other_ours = {
        code for key, rows in other_ours.items() if key in window_keys
        for code in rows
    }
    # The first month legacy holds for an issuer, in either of its markets: a
    # row of ours older than that is the source filing revenue from before the
    # issuer was listed, which legacy had no page to read it from.
    first_in_legacy: dict[str, tuple[int, int]] = {}
    last_in_legacy: dict[str, tuple[int, int]] = {}
    for rows_by_month in (theirs, other_theirs):
        for key, rows in rows_by_month.items():
            for code in rows:
                if code not in first_in_legacy or key < first_in_legacy[code]:
                    first_in_legacy[code] = key
                if code not in last_in_legacy or key > last_in_legacy[code]:
                    last_in_legacy[code] = key

    def note(kind: str, key, code: str, field: str | None, ours_value, legacy_value) -> None:
        differences[kind] += 1
        if field:
            differing_fields[field] += 1
        bucket = examples.setdefault(kind, [])
        # An unexplained difference is the report's point, so it is listed in
        # full rather than sampled.
        if len(bucket) < (50 if "unexplained" in kind else 5):
            bucket.append({
                "period": f"{key[0]:04d}-{key[1]:02d}",
                "security_code": code,
                "field": field,
                "ours": None if ours_value is None else str(ours_value),
                "legacy": None if legacy_value is None else str(legacy_value),
            })

    def stale_revenue(code, key, ours_value, legacy_value) -> bool:
        """The source's own next page calls our 當月營收 last month's, not legacy's."""
        later = ours.get(following(key), {}).get(code)
        return (
            later is not None
            and later["revenue_last_month"] is not None
            and ours_value is not None
            and Decimal(later["revenue_last_month"]) == Decimal(ours_value)
            and Decimal(later["revenue_last_month"])
            != as_decimal(legacy_value, thousand=True)
        )

    def classify_value(field, code, key, ours_value, legacy_value) -> str:
        if field == "note":
            if ours_value == "NA" and legacy_value is None:
                return "note_differs:legacy_read_NA_as_missing"
            if isinstance(ours_value, str) and isinstance(legacy_value, str):
                ours_text = WHITESPACE.sub(" ", ours_value)
                legacy_text = WHITESPACE.sub(" ", legacy_value)
                if ours_text == legacy_text:
                    return "note_differs:legacy_collapsed_whitespace"
                if (
                    encoded(ours_text) is not None
                    and encoded(ours_text) == encoded(legacy_text)
                ):
                    return "note_differs:legacy_decoded_the_same_bytes_differently"
                if "\ufffd" in legacy_value:
                    return "note_differs:legacy_lost_bytes_decoding"
        if field == "revenue" and stale_revenue(code, key, ours_value, legacy_value):
            return "value_differs:legacy_capture_stale"
        if field != "revenue" and (
            (code, key) in stale
            or any((code, month) in stale for month in INPUT_MONTHS[field](key))
        ):
            return "value_differs:restated_by_a_correction"
        if field in RESTATES:
            month_of, restated = RESTATES[field]
            held = ours.get(month_of(key), {}).get(code)
            if (
                held is not None
                and held[restated] is not None
                and ours_value is not None
                and Decimal(held[restated]) == Decimal(ours_value)
            ):
                # Ours is what we hold for the month it restates; legacy's copy
                # is not, so legacy's row disagrees with the series it came from.
                return "value_differs:legacy_disagrees_with_the_month_it_restates"
        if field == "cumulative_revenue" and consistent_cumulative(
            code, key, ours, theirs
        ):
            return "value_differs:legacy_row_internally_inconsistent"
        if field in KNOWN_LATE_REPUBLICATION.get(
            (source, code, f"{key[0]:04d}-{key[1]:02d}"), ()
        ):
            return "value_differs:known_late_republication"
        return f"value_differs:unexplained:{field}"

    # Pass one: which rows the source corrected after legacy captured them.
    # A comparative of another month can only be blamed on a correction that is
    # already proven, so the proving has to finish before the classifying.
    stale: set[tuple[str, tuple[int, int]]] = set()
    for key in [(p.year, p.month) for p in month_keys]:
        for code, theirs_row in theirs.get(key, {}).items():
            ours_row = ours.get(key, {}).get(code)
            if ours_row is None:
                continue
            if differs(ours_row["revenue"], theirs_row["revenue"], thousand=True) and (
                stale_revenue(code, key, ours_row["revenue"], theirs_row["revenue"])
            ):
                stale.add((code, key))

    for key in [(p.year, p.month) for p in month_keys]:
        ours_month = ours.get(key, {})
        theirs_month = theirs.get(key, {})
        for code, theirs_row in sorted(theirs_month.items()):
            ours_row = ours_month.get(code)
            if ours_row is None:
                if code in other_ours.get(key, {}):
                    kind = "absent_from_source:stored_under_the_other_market"
                elif code not in ever_in_ours and code not in ever_in_other_ours:
                    # The source regenerates the page by current status; an
                    # issuer that left both markets is on no month's page.
                    kind = "absent_from_source:issuer_on_no_page_of_either_market"
                else:
                    kind = "absent_from_source:unexplained"
                note(kind, key, code, None, None, theirs_row["revenue"])
                continue
            compared += 1
            # Amounts and the note first, then the percentages: a percentage is
            # explained by the amounts it is computed from, so those have to be
            # classified before it can be asked about.
            classes: dict[str, str] = {}
            for field, (_, thousand) in LEGACY.items():
                if field in PERCENT_INPUTS:
                    continue
                if differs(ours_row[field], theirs_row[field], thousand=thousand):
                    classes[field] = classify_value(
                        field, code, key, ours_row[field], theirs_row[field]
                    )
            for field, inputs in PERCENT_INPUTS.items():
                if not differs(ours_row[field], theirs_row[field], thousand=False):
                    continue
                kind = classify_value(
                    field, code, key, ours_row[field], theirs_row[field]
                )
                if kind.startswith("value_differs:unexplained") and any(
                    name in classes
                    and not classes[name].startswith("value_differs:unexplained")
                    for name in inputs
                ):
                    kind = "value_differs:follows_the_amounts_it_is_computed_from"
                classes[field] = kind
            for field, kind in classes.items():
                note(kind, key, code, field, ours_row[field], theirs_row[field])
        for code, ours_row in sorted(ours_month.items()):
            if code in theirs_month:
                continue
            if code in foreign.get(key, set()):
                kind = "absent_from_legacy:foreign_page"
            elif code in other_theirs.get(key, {}):
                kind = "absent_from_legacy:legacy_filed_it_under_the_other_market"
            elif key < first_in_legacy.get(code, (9999, 12)) and (
                code not in priced or priced[code] > month_end(key)
            ):
                kind = "absent_from_legacy:issuer_listed_after_that_month"
            elif (
                first_in_legacy.get(code, (9999, 12))
                < key
                < last_in_legacy.get(code, (0, 1))
            ):
                # Legacy has the issuer on both sides of this month: the row it
                # is missing is its own gap, not something the page lacks.
                kind = "absent_from_legacy:legacy_missed_that_month"
            else:
                kind = "absent_from_legacy:unexplained"
            note(kind, key, code, None, ours_row["revenue"], None)
    return {
        "market": market,
        "legacy_market": legacy_market,
        "stored_rows": sum(
            len(rows) for key, rows in ours.items() if key in window_keys
        ),
        "legacy_rows": sum(len(rows) for rows in theirs.values()),
        "compared_rows": compared,
        "differences": dict(sorted(differences.items())),
        "differing_fields": dict(differing_fields),
        "difference_examples": examples,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reconcile-monthly-revenue")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--legacy-database-url", default=os.getenv("LEGACY_DATABASE_URL"))
    parser.add_argument("--start", default="2020-01")
    parser.add_argument("--end", default="2026-08")
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    args = parser.parse_args(argv)
    if not args.database_url or not args.legacy_database_url:
        parser.error("--database-url and --legacy-database-url are required")
    start = RevenuePeriod(*(int(part) for part in args.start.split("-")))
    end = RevenuePeriod(*(int(part) for part in args.end.split("-")))
    months = [f"{p.year:04d}-{p.month:02d}" for p in periods(start, end)]
    window = (date(start.year, start.month, 1), date(end.year, end.month, 1))

    engine = sa.create_engine(args.database_url)
    legacy_engine = sa.create_engine(args.legacy_database_url)
    report: dict = {
        "window": {"start": args.start, "end": args.end, "months": len(months)},
        "sources": {},
    }
    try:
        with engine.connect() as connection, legacy_engine.connect() as legacy:
            scans = {
                source: page_scan(
                    connection, source, adapter_class, months, args.raw_root
                )
                for source, (_, _, adapter_class, _) in MARKETS.items()
            }
            # One month past the window, for lookups only: the proof that a
            # value is stale is the *next* month's 上月營收, so the window's
            # last month has none to appeal to if the load stops with it.
            lookup_end = month_end((end.year, end.month))
            ours_by_source = {
                source: stored(connection, source, window[0], lookup_end)
                for source in MARKETS
            }
            priced = first_priced(connection)
            for source, (market, _, _, _) in MARKETS.items():
                coverage = CoverageValidator().report(
                    connection, dataset_code=DATASET, market=market,
                    start=window[0], end=window[1],
                )
                scan = dict(scans[source])
                by_month = scan.pop("by_month")
                foreign_issuers = sorted(
                    {code for pages in by_month.values() for code in pages.get("1", ())}
                )
                report["sources"][source] = {
                    "import": import_counts(connection, source, months),
                    "quarantines": quarantines(connection, source, months),
                    "coverage": {
                        "expected_months": len(coverage.expected),
                        "observed_months": len(coverage.observed),
                        "missing_months": [d.isoformat() for d in coverage.missing],
                        "unexpected_months": [d.isoformat() for d in coverage.unexpected],
                        "is_complete": coverage.is_complete,
                    },
                    "page_scan": {
                        **scan,
                        "distinct_foreign_issuers": len(foreign_issuers),
                        "foreign_issuer_sample": foreign_issuers[:10],
                    },
                    "legacy_reconciliation": compare(
                        connection, legacy, source, start, end, scans,
                        ours_by_source, priced,
                    ),
                }
    finally:
        engine.dispose()
        legacy_engine.dispose()

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    clean = all(
        entry["coverage"]["is_complete"]
        and not entry["page_scan"]["months_with_a_company_on_both_pages"]
        and not entry["page_scan"]["months_missing_a_page"]
        and set(entry["legacy_reconciliation"]["differences"]) <= EXPLAINED
        for entry in report["sources"].values()
    )
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
