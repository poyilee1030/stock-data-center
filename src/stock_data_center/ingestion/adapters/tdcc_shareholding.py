"""TDCC shareholding distribution: OpenData `id=1-5`, live and archived.

One weekly whole-market file, six columns, one 資料日期 (audit §4.9). OpenData
serves the latest week only, and everything before the first forward capture
exists solely in the archive at `~/GitHubLL/my_stock_project/data/raw/
shareholding` (ROADMAP §14). Every archived file carries the same OpenData
header, so one parser reads both and the two adapters differ only in where the
bytes come from.

**The filename is not authoritative.** `2020/20200619.CSV` and
`2020/20200619.zip` both hold 資料日期 `20200612`: they were named for their
download date, and OpenData still served the previous week. Keying on the name
would have invented a 2020-06-19 week and hidden that 2020-06-20 was missing.
So the content date decides, and a file whose name disagrees is refused rather
than quietly renamed.

**The distribution profile** (`tdcc-opendata-v1`, Step 6) is levels 1-15
holding, 16 差異數調整, 17 合計. The bulk file always emits all seventeen,
zero-filling the adjustment row when there is none; the portal, which is the
same publisher rendering the same week, shows only sixteen rows in that case,
with 合計 in position 16. Two facts about the adjustment row, verified against
the portal on 2026-09-11 for 0056, 00400A, 00401A and 00404A, decide how it is
stored:

* **The published sign is negative.** The bulk file states the magnitude
  unsigned; the portal states `-28,164` where the bulk file says `28164`, and
  `合計 = Σ levels 1-15 − 差異數調整` holds for all 1,377,971 security-weeks
  in the archive, with no exception either way. So the sign is the publisher's,
  recovered from the publisher's other rendering, not one we invented.
* **It has no holder count.** 說明4 defines the row as the difference between
  the levels' total and the issuer's issued shares, caused by insufficient
  sell balances on the business day before the data date. A difference has no
  holders, and the portal leaves that cell blank. The bulk file's fixed six
  columns put a small integer there (19,801 rows over 368 of 376 weeks); it is
  not stored, it is counted, and the raw artifact keeps it.
"""

from __future__ import annotations

import csv
import io
import re
import tempfile
import zipfile
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import ClassVar

from stock_data_center.ingestion.models import (
    ParsedTDCCShareholding,
    SourceDataError,
    SourceResource,
    TDCCRejectedSecurity,
    TDCCShareholdingRequest,
    TDCCShareholdingRow,
)
from stock_data_center.tdcc import (
    TDCC_OPENDATA_V1,
    TDCCBucketObservation,
    TDCCSnapshotObservation,
)

# ROADMAP §14: the archive stays where the owner keeps it, outside this repo.
DEFAULT_ARCHIVE_ROOT = Path.home() / "GitHubLL/my_stock_project/data/raw/shareholding"

OPENDATA_URL = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"
DATASET_CODE = "tdcc_snapshot"
SOURCE = "tdcc_opendata"

HEADER = ("資料日期", "證券代號", "持股分級", "人數", "股數", "占集保庫存數比例%")
HEADER_VARIANT = "tdcc_opendata_1_5_6"

# The profile's bucket codes, and the role each one plays in it (Step 6).
HOLDING_LEVELS = tuple(str(level) for level in range(1, 16))
ADJUSTMENT_LEVEL = "16"
TOTAL_LEVEL = "17"
PROFILE_LEVELS = (*HOLDING_LEVELS, ADJUSTMENT_LEVEL, TOTAL_LEVEL)

INTEGER = re.compile(r"-?\d+")
DECIMAL = re.compile(r"-?\d+(?:\.\d+)?")
DATE_COMPACT = re.compile(r"(\d{4})(\d{2})(\d{2})")
DATE_SLASHED = re.compile(r"(\d{4})/(\d{2})/(\d{2})")
# A date anywhere in the filename: the archive names a week three ways —
# `20240105.7z`, `20201008_集保戶股權分散表20201008.7z`, and OpenData's own
# `TDCC_OD_1-5_20260918.csv`. All three agree with their content date.
FILENAME_DATE = re.compile(r"(20\d{2})(\d{2})(\d{2})")

ZIP_MAGIC = b"PK\x03\x04"
SEVENZIP_MAGIC = b"7z\xbc\xaf\x27\x1c"


def parse_shareholding(
    content: bytes, *, source: str, expected_date: date | None
) -> ParsedTDCCShareholding:
    """One weekly file, from whichever container it arrived in."""
    container, member, payload = _unpack(content, source)
    try:
        text = payload.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as error:
        # A payload cut in the middle of a multi-byte character is truncation,
        # not a different encoding, and saying so keeps the two apart in the
        # manifest. Anything else really is an encoding the parser does not
        # know.
        raise SourceDataError(
            "truncated_payload"
            if error.reason == "unexpected end of data"
            else "invalid_encoding",
            f"{source}: {error}",
        ) from error
    # Ten archived files carry a second BOM after `utf-8-sig` (audit §4.9).
    while text.startswith("﻿"):
        text = text[1:]
    # A payload that does not end in a newline was cut off mid-write. The
    # complete securities before the cut are still published facts, so the
    # partial line is dropped and the file is reported as truncated; the
    # security it belonged to fails its profile below and is quarantined.
    cut_mid_row = bool(text) and not text.endswith(("\n", "\r"))
    if cut_mid_row:
        if "\n" not in text:
            # Cut before the first newline: there is no header to read, and
            # calling that an empty response would hide that bytes were lost.
            raise SourceDataError(
                "truncated_payload",
                f"{source}: the payload ends mid-row before its first newline",
            )
        text = text[: text.rfind("\n") + 1]

    reader = csv.reader(io.StringIO(text))
    try:
        header = tuple(cell.strip() for cell in next(reader))
    except StopIteration:
        raise SourceDataError("empty_response", f"{source}: the file has no rows") from None
    if header != HEADER:
        raise SourceDataError(
            "schema_mismatch", f"{source}: header is {header!r}"
        )

    snapshot_date: date | None = None
    date_format = ""
    levels: dict[str, dict[str, tuple[str, str, str]]] = {}
    order: list[str] = []
    rejected: dict[str, TDCCRejectedSecurity] = {}
    source_rows = 0
    for number, row in enumerate(reader, 2):
        if not row:
            continue
        source_rows += 1
        if len(row) != len(HEADER):
            raise SourceDataError(
                "malformed_row",
                f"{source} row {number} has {len(row)} cells, not {len(HEADER)}",
            )
        row_date, row_format = _snapshot_date(row[0], source, number)
        if snapshot_date is None:
            snapshot_date, date_format = row_date, row_format
        elif row_date != snapshot_date:
            # One file is one published week. Two dates would mean the file is
            # two weeks and nothing says which one it answers.
            raise SourceDataError(
                "multiple_snapshot_dates",
                f"{source} row {number} is {row_date.isoformat()}, after "
                f"{snapshot_date.isoformat()}",
            )
        code = row[1].strip()
        if not code:
            raise SourceDataError(
                "invalid_identity", f"{source} row {number} has no security code"
            )
        level = row[2].strip()
        if code not in levels:
            levels[code] = {}
            order.append(code)
        if level in levels[code]:
            rejected.setdefault(
                code,
                TDCCRejectedSecurity(
                    security_code=code,
                    reason_code="duplicate_holding_level",
                    detail=f"{code} states holding level {level} twice",
                ),
            )
            continue
        levels[code][level] = (row[3].strip(), row[4].strip(), row[5].strip())

    if snapshot_date is None:
        raise SourceDataError("empty_response", f"{source}: the file has no data rows")
    if expected_date is not None and snapshot_date != expected_date:
        raise SourceDataError(
            "snapshot_date_mismatch",
            f"{source}: the file is named for {expected_date.isoformat()} but "
            f"states 資料日期 {snapshot_date.isoformat()}; the content date "
            "decides and the name is not trusted",
        )

    rows: list[TDCCShareholdingRow] = []
    dropped_adjustment_holders = 0
    for code in order:
        if code in rejected:
            continue
        found = levels[code]
        if missing := [level for level in PROFILE_LEVELS if level not in found]:
            rejected[code] = TDCCRejectedSecurity(
                security_code=code,
                reason_code="incomplete_distribution",
                detail=(
                    f"{code} is missing holding levels {', '.join(missing)} of "
                    f"profile {TDCC_OPENDATA_V1}"
                ),
            )
            continue
        if unknown := sorted(set(found) - set(PROFILE_LEVELS)):
            rejected[code] = TDCCRejectedSecurity(
                security_code=code,
                reason_code="unknown_holding_level",
                detail=(
                    f"{code} states holding levels {', '.join(unknown)}, which "
                    f"profile {TDCC_OPENDATA_V1} does not define"
                ),
            )
            continue
        try:
            buckets, dropped = _buckets(code, found, source)
        except SourceDataError as error:
            rejected[code] = TDCCRejectedSecurity(
                security_code=code,
                reason_code=error.reason_code,
                detail=str(error),
            )
            continue
        dropped_adjustment_holders += dropped
        rows.append(
            TDCCShareholdingRow(
                security_code=code,
                observation=TDCCSnapshotObservation(
                    snapshot_date=snapshot_date,
                    distribution_schema=TDCC_OPENDATA_V1,
                    distribution=buckets,
                ),
            )
        )

    # A cut that happens to land on a newline leaves no partial row, so the
    # byte-level check above cannot see it. What it does leave is a last
    # security missing the rest of its levels, which is the same signal from
    # the other end. Only an exact cut at a security boundary — one row in
    # seventeen — still looks complete here, and that is what Step 24-b's
    # week-over-week security count is for.
    truncated = cut_mid_row or (bool(order) and order[-1] in rejected)

    return ParsedTDCCShareholding(
        snapshot_date=snapshot_date,
        rows=tuple(rows),
        rejected=tuple(rejected[code] for code in order if code in rejected),
        header_variant=HEADER_VARIANT,
        source_fields=HEADER,
        source_rows=source_rows,
        container=container,
        member_name=member,
        date_format=date_format,
        truncated=truncated,
        dropped_adjustment_holder_counts=dropped_adjustment_holders,
    )


def _buckets(
    code: str, found: dict[str, tuple[str, str, str]], source: str
) -> tuple[tuple[TDCCBucketObservation, ...], int]:
    buckets: list[TDCCBucketObservation] = []
    dropped = 0
    for level in PROFILE_LEVELS:
        holders, shares, percent = found[level]
        share_count = _integer(shares, source, code, level, "股數")
        share_percent = _decimal(percent, source, code, level, "占集保庫存數比例%")
        if level == ADJUSTMENT_LEVEL:
            # 說明4: the row is a difference, so it has no holders. The bulk
            # file's six fixed columns still carry a number there, which the
            # portal leaves blank; it is counted and left in the raw artifact.
            stated = _integer(holders, source, code, level, "人數")
            dropped += bool(stated)
            buckets.append(
                TDCCBucketObservation(
                    bucket_code=level,
                    holder_count=None,
                    shares=_as_published_sign(share_count),
                    ownership_percent=_as_published_sign(share_percent),
                )
            )
            continue
        holder_count = _integer(holders, source, code, level, "人數")
        if holder_count < 0 or share_count < 0 or share_percent < 0:
            raise SourceDataError(
                "negative_holding_level",
                f"{code} holding level {level} is negative: "
                f"{holders}/{shares}/{percent}",
            )
        if level != TOTAL_LEVEL and share_percent > 100:
            # Checked here so it quarantines this security, the way every
            # other defect in a distribution does. The writer's profile
            # validation and the row trigger enforce the same rule, but they
            # raise inside the transaction and would fail the whole week over
            # one security. The published 合計 has no ceiling (module
            # docstring), so only the holding levels are checked.
            raise SourceDataError(
                "holding_level_above_one_hundred",
                f"{code} holding level {level} owns {percent}% of custody",
            )
        buckets.append(
            TDCCBucketObservation(
                bucket_code=level,
                holder_count=int(holder_count),
                shares=share_count,
                ownership_percent=share_percent,
            )
        )
    return tuple(buckets), dropped


def _as_published_sign(value: Decimal) -> Decimal:
    """The adjustment's own sign: negative, as its publisher renders it.

    The bulk file states the magnitude and the portal states it negative
    (module docstring), so a positive value is negated and an already-signed
    one is left alone. Zero stays zero rather than becoming `-0`.
    """
    return -value if value > 0 else value


def _snapshot_date(text: str, source: str, number: int) -> tuple[date, str]:
    value = text.strip()
    if match := DATE_COMPACT.fullmatch(value):
        fmt = "YYYYMMDD"
    elif match := DATE_SLASHED.fullmatch(value):
        # One archived file, `2019/20190628.zip`, writes it this way.
        fmt = "YYYY/MM/DD"
    else:
        raise SourceDataError(
            "unrecognised_value",
            f"{source} row {number} 資料日期 is {text!r}",
        )
    try:
        return date(*(int(part) for part in match.groups())), fmt
    except ValueError as error:
        raise SourceDataError(
            "unrecognised_value", f"{source} row {number} 資料日期 is {text!r}"
        ) from error


def _integer(text: str, source: str, code: str, level: str, field: str) -> Decimal:
    value = text.replace(",", "").strip()
    if not INTEGER.fullmatch(value):
        raise SourceDataError(
            "unrecognised_value",
            f"{source} {code} level {level} {field} is {text!r}",
        )
    return Decimal(value)


def _decimal(text: str, source: str, code: str, level: str, field: str) -> Decimal:
    value = text.replace(",", "").strip()
    if not DECIMAL.fullmatch(value):
        raise SourceDataError(
            "unrecognised_value",
            f"{source} {code} level {level} {field} is {text!r}",
        )
    try:
        return Decimal(value)
    except InvalidOperation as error:  # pragma: no cover - the regex ran first
        raise SourceDataError(
            "unrecognised_value",
            f"{source} {code} level {level} {field} is {text!r}",
        ) from error


def _unpack(content: bytes, source: str) -> tuple[str, str | None, bytes]:
    """The one CSV inside the payload, whatever container holds it.

    The container is read from the payload's own magic bytes rather than a
    filename: the archive mixes `.csv`, `.CSV`, `.zip` and `.7z`, and one
    `.7z` (`2021/20210806.7z`) also holds a directory and four stray scraper
    logs, so the member has to be chosen rather than assumed.
    """
    if content.startswith(ZIP_MAGIC):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = [
                name
                for name in archive.namelist()
                if not name.endswith("/") and name.lower().endswith(".csv")
            ]
            name = _one_member(members, source, "zip")
            return "zip", name, archive.read(name)
    if content.startswith(SEVENZIP_MAGIC):
        import py7zr

        with tempfile.TemporaryDirectory() as directory:
            with py7zr.SevenZipFile(io.BytesIO(content)) as archive:
                archive.extractall(path=directory)
            extracted = sorted(
                path
                for path in Path(directory).rglob("*")
                if path.is_file() and path.suffix.lower() == ".csv"
            )
            names = [
                str(path.relative_to(directory)).replace("\\", "/")
                for path in extracted
            ]
            name = _one_member(names, source, "7z")
            return "7z", name, extracted[names.index(name)].read_bytes()
    return "csv", None, content


def _one_member(members: list[str], source: str, container: str) -> str:
    if not members:
        raise SourceDataError(
            "archive_member_missing", f"{source}: the {container} holds no CSV"
        )
    if len(members) > 1:
        # One week, one table. Two would mean the container holds two answers
        # and nothing says which is the week.
        raise SourceDataError(
            "ambiguous_archive_member",
            f"{source}: the {container} holds {len(members)} CSVs: {members}",
        )
    return members[0]


class TDCCOpenDataAdapter:
    """The live OpenData file, which is always the latest published week."""

    dataset_code = DATASET_CODE
    source = SOURCE
    version = "tdcc-opendata-1-5:v1"
    variants: ClassVar[dict[str, tuple[str, ...]]] = {HEADER_VARIANT: HEADER}

    def resource(self, request: TDCCShareholdingRequest) -> SourceResource:
        return SourceResource(
            resource_key=f"{SOURCE}:opendata:1-5",
            source_uri=OPENDATA_URL,
            headers=(("accept", "text/csv"),),
        )

    def parse(
        self, content: bytes, request: TDCCShareholdingRequest
    ) -> ParsedTDCCShareholding:
        # `snapshot_date` is what the caller expects OpenData to be serving.
        # It is checked when given, because a week later than expected means
        # the next file was published while the job ran.
        return parse_shareholding(
            content, source=self.source, expected_date=request.snapshot_date
        )


class LegacyTDCCArchiveAdapter:
    """One archived week, read as `legacy_archive` bytes (CLAUDE.md §75).

    These are the only archived files that are official response bytes: the
    legacy scraper saved the OpenData download whole, header and all
    (ROADMAP §14). They are still read as `legacy_archive`, because what the
    file proves about publication time is the archive's story, not a fetch.
    """

    dataset_code = DATASET_CODE
    source = SOURCE
    version = "tdcc-archive-1-5:v1"
    variants: ClassVar[dict[str, tuple[str, ...]]] = {HEADER_VARIANT: HEADER}

    def __init__(self, *, archive_root: Path | None = None) -> None:
        self._root = Path(archive_root) if archive_root else DEFAULT_ARCHIVE_ROOT

    def resource(self, request: TDCCShareholdingRequest) -> SourceResource:
        if request.snapshot_date is None:
            raise ValueError("an archived week needs its snapshot date")
        day = request.snapshot_date
        return SourceResource(
            resource_key=f"{SOURCE}:archive:{day.isoformat()}",
            # A pattern, not a file: 52 content dates have more than one copy
            # and the names take three shapes, so the fetcher resolves it and
            # records which file answered (see `TDCCArchiveFetcher`).
            source_uri=str(self._root / str(day.year) / f"*{day:%Y%m%d}*"),
        )

    def parse(
        self, content: bytes, request: TDCCShareholdingRequest
    ) -> ParsedTDCCShareholding:
        return parse_shareholding(
            content, source=self.source, expected_date=request.snapshot_date
        )


def filename_date(path: Path) -> date | None:
    """The date an archived filename states, if it states one.

    Used to refuse a file whose name and content disagree without trusting the
    name for anything else: three naming shapes are in use and all of them put
    the date somewhere in the stem.
    """
    match = FILENAME_DATE.search(path.stem)
    if match is None:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None
