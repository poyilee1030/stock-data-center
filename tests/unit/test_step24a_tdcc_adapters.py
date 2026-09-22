"""Step 24-a — the TDCC shareholding parser, on the real file's variants.

Every fixture is a slice of a real archived file, rows kept byte for byte, so
the defects under test are the ones the archive actually holds (audit §4.9).
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from stock_data_center.ingestion.adapters.tdcc_shareholding import (
    LegacyTDCCArchiveAdapter,
    TDCCOpenDataAdapter,
    filename_date,
    parse_shareholding,
)
from stock_data_center.ingestion.http import TDCCArchiveFetcher
from stock_data_center.ingestion.models import (
    SourceDataError,
    TDCCShareholdingRequest,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
LATEST = (FIXTURES / "tdcc_od_1_5_20260918.csv").read_bytes()
SLASHED = (FIXTURES / "tdcc_od_1_5_20190628_slashed.csv").read_bytes()
DOUBLE_BOM = (FIXTURES / "tdcc_od_1_5_20200430_double_bom.csv").read_bytes()
TRUNCATED = (FIXTURES / "tdcc_od_1_5_20231020_truncated.csv").read_bytes()
MISNAMED = (FIXTURES / "tdcc_od_1_5_20200612_named_20200619.csv").read_bytes()

SOURCE = "tdcc_opendata"


def parse(content: bytes, expected: date | None = None):
    return parse_shareholding(content, source=SOURCE, expected_date=expected)


def buckets(parsed, code: str) -> dict[str, tuple[int | None, Decimal, Decimal]]:
    row = next(item for item in parsed.rows if item.security_code == code)
    return {
        bucket.bucket_code: (
            bucket.holder_count,
            bucket.shares,
            bucket.ownership_percent,
        )
        for bucket in row.observation.distribution
    }


def zipped(content: bytes, *names: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, content)
    return buffer.getvalue()


def seven_zipped(members: dict[str, bytes]) -> bytes:
    py7zr = pytest.importorskip("py7zr")
    buffer = io.BytesIO()
    with py7zr.SevenZipFile(buffer, "w") as archive:
        for name, payload in members.items():
            archive.writef(io.BytesIO(payload), name)
    return buffer.getvalue()


def test_one_week_parses_every_level_as_published() -> None:
    parsed = parse(LATEST, date(2026, 9, 18))
    assert parsed.snapshot_date == date(2026, 9, 18)
    assert parsed.container == "csv"
    assert parsed.date_format == "YYYYMMDD"
    assert parsed.header_variant == "tdcc_opendata_1_5_6"
    # The file pads codes with spaces; identity is the trimmed code.
    assert [row.security_code for row in parsed.rows] == ["0056", "1101", "2330"]
    levels = buckets(parsed, "2330")
    assert levels["1"] == (2_496_562, Decimal("291447275"), Decimal("1.12"))
    assert levels["17"] == (3_050_518, Decimal("25932370067"), Decimal("100.00"))


def test_the_adjustment_row_is_stored_negative_without_a_holder_count() -> None:
    """說明4: the row is a difference, and its publisher renders it negative."""
    parsed = parse(LATEST, date(2026, 9, 18))
    holders, shares, percent = buckets(parsed, "0056")["16"]
    assert holders is None
    assert shares == Decimal("-4000")
    assert percent == Decimal("0.00")
    # 0056 and 2330 each state 1 in the 人數 column, which the portal leaves
    # blank; it is counted rather than stored.
    assert parsed.dropped_adjustment_holder_counts == 2


def test_a_security_with_no_adjustment_keeps_a_zero_row() -> None:
    parsed = parse(LATEST, date(2026, 9, 18))
    assert buckets(parsed, "1101")["16"] == (None, Decimal("0"), Decimal("0.00"))


def test_the_published_total_may_exceed_one_hundred_percent() -> None:
    parsed = parse(DOUBLE_BOM, date(2020, 4, 30))
    assert buckets(parsed, "00673R")["17"][2] == Decimal("101.00")


def test_a_second_bom_is_stripped() -> None:
    assert DOUBLE_BOM.startswith(b"\xef\xbb\xbf\xef\xbb\xbf")
    parsed = parse(DOUBLE_BOM, date(2020, 4, 30))
    assert parsed.snapshot_date == date(2020, 4, 30)
    assert len(parsed.rows) == 2


def test_a_slashed_data_date_parses_and_is_reported() -> None:
    parsed = parse(SLASHED, date(2019, 6, 28))
    assert parsed.snapshot_date == date(2019, 6, 28)
    assert parsed.date_format == "YYYY/MM/DD"


def test_the_content_date_decides_and_a_disagreeing_name_is_refused() -> None:
    """`20200619.CSV` holds 資料日期 20200612 (audit §4.9)."""
    assert filename_date(Path("20200619.CSV")) == date(2020, 6, 19)
    with pytest.raises(SourceDataError) as error:
        parse(MISNAMED, date(2020, 6, 19))
    assert error.value.reason_code == "snapshot_date_mismatch"
    # Asked for the week it really holds, it parses.
    assert parse(MISNAMED, date(2020, 6, 12)).snapshot_date == date(2020, 6, 12)


def test_a_truncated_file_imports_its_complete_securities_and_quarantines_the_cut() -> None:
    parsed = parse(TRUNCATED, date(2023, 10, 20))
    assert parsed.truncated is True
    assert [row.security_code for row in parsed.rows] == ["2330"]
    assert [item.security_code for item in parsed.rejected] == ["8162"]
    assert parsed.rejected[0].reason_code == "incomplete_distribution"


def test_a_zip_and_a_7z_read_the_same_as_the_csv() -> None:
    expected = parse(LATEST, date(2026, 9, 18))
    from_zip = parse(zipped(LATEST, "20260918.csv"), date(2026, 9, 18))
    from_7z = parse(seven_zipped({"20260918.CSV": LATEST}), date(2026, 9, 18))
    assert from_zip.container == "zip"
    assert from_zip.member_name == "20260918.csv"
    assert from_7z.container == "7z"
    assert from_7z.member_name == "20260918.CSV"
    for parsed in (from_zip, from_7z):
        assert buckets(parsed, "0056") == buckets(expected, "0056")


def test_a_7z_holding_stray_scraper_logs_still_finds_the_one_csv() -> None:
    """`2021/20210806.7z` also holds a directory and four TraceLog files."""
    payload = seven_zipped(
        {
            "20210806/TraceLog20210722.Txt": b"noise\n",
            "20210806/TraceLog20210806 - 複製.Txt": b"noise\n",
            "20210806.csv": LATEST,
        }
    )
    parsed = parse(payload, date(2026, 9, 18))
    assert parsed.member_name == "20210806.csv"
    assert len(parsed.rows) == 3


def test_two_csvs_in_one_container_are_ambiguous() -> None:
    with pytest.raises(SourceDataError) as error:
        parse(zipped(LATEST, "a.csv", "b.csv"), date(2026, 9, 18))
    assert error.value.reason_code == "ambiguous_archive_member"


def test_a_container_without_a_csv_is_refused() -> None:
    with pytest.raises(SourceDataError) as error:
        parse(zipped(LATEST, "20260918.txt"), date(2026, 9, 18))
    assert error.value.reason_code == "archive_member_missing"


def test_two_data_dates_in_one_file_are_refused() -> None:
    text = LATEST.decode("utf-8-sig")
    lines = text.splitlines(keepends=True)
    lines[-1] = lines[-1].replace("20260918", "20260911", 1)
    with pytest.raises(SourceDataError) as error:
        parse("".join(lines).encode("utf-8"))
    assert error.value.reason_code == "multiple_snapshot_dates"


def test_a_changed_header_is_refused() -> None:
    text = LATEST.decode("utf-8-sig").replace("持股分級", "持股級距", 1)
    with pytest.raises(SourceDataError) as error:
        parse(text.encode("utf-8"))
    assert error.value.reason_code == "schema_mismatch"


def test_a_duplicated_level_quarantines_that_security_alone() -> None:
    lines = LATEST.decode("utf-8-sig").splitlines(keepends=True)
    doubled = [line for line in lines if ",0056  ,1," in line]
    parsed = parse("".join(lines + doubled).encode("utf-8"), date(2026, 9, 18))
    assert [item.security_code for item in parsed.rejected] == ["0056"]
    assert parsed.rejected[0].reason_code == "duplicate_holding_level"
    assert [row.security_code for row in parsed.rows] == ["1101", "2330"]


def test_a_level_outside_the_profile_quarantines_that_security_alone() -> None:
    lines = LATEST.decode("utf-8-sig").splitlines(keepends=True)
    extra = next(line for line in lines if ",0056  ,1," in line).replace(
        ",1,", ",18,", 1
    )
    parsed = parse("".join(lines + [extra]).encode("utf-8"), date(2026, 9, 18))
    assert [item.security_code for item in parsed.rejected] == ["0056"]
    assert parsed.rejected[0].reason_code == "unknown_holding_level"


def test_a_negative_holding_level_quarantines_that_security_alone() -> None:
    lines = LATEST.decode("utf-8-sig").splitlines(keepends=True)
    changed = [
        line.replace(",2496562,291447275,", ",2496562,-291447275,")
        if ",2330  ,1," in line
        else line
        for line in lines
    ]
    parsed = parse("".join(changed).encode("utf-8"), date(2026, 9, 18))
    assert [item.security_code for item in parsed.rejected] == ["2330"]
    assert parsed.rejected[0].reason_code == "negative_holding_level"


def test_an_unparsable_number_quarantines_that_security_alone() -> None:
    text = LATEST.decode("utf-8-sig").replace(",3050518,", ",n/a,", 1)
    parsed = parse(text.encode("utf-8"), date(2026, 9, 18))
    assert [item.security_code for item in parsed.rejected] == ["2330"]
    assert parsed.rejected[0].reason_code == "unrecognised_value"


def test_the_live_adapter_requests_opendata_and_the_archive_one_a_pattern() -> None:
    live = TDCCOpenDataAdapter().resource(TDCCShareholdingRequest())
    assert live.source_uri == "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"
    assert live.resource_key == "tdcc_opendata:opendata:1-5"
    archived = LegacyTDCCArchiveAdapter(archive_root=Path("/archive")).resource(
        TDCCShareholdingRequest(date(2024, 1, 5))
    )
    assert archived.source_uri == "/archive/2024/*20240105*"
    assert archived.resource_key == "tdcc_opendata:archive:2024-01-05"
    with pytest.raises(ValueError):
        LegacyTDCCArchiveAdapter().resource(TDCCShareholdingRequest())


def test_the_archive_fetcher_picks_one_copy_and_records_the_others(
    tmp_path: Path,
) -> None:
    """52 content dates keep two copies, and each pair agrees (audit §4.9)."""
    year = tmp_path / "2020"
    year.mkdir()
    (year / "20200103.csv").write_bytes(LATEST)
    (year / "20200103.zip").write_bytes(zipped(LATEST, "20200103.csv"))
    fetcher = TDCCArchiveFetcher()
    fetched = fetcher.fetch(
        LegacyTDCCArchiveAdapter(archive_root=tmp_path).resource(
            TDCCShareholdingRequest(date(2020, 1, 3))
        )
    )
    assert fetched.source_uri.endswith("20200103.csv")
    assert fetcher.last_candidates == ("20200103.csv", "20200103.zip")
    assert fetcher.last_mtime is not None


def test_a_missing_archived_week_is_a_fetch_failure(tmp_path: Path) -> None:
    (tmp_path / "2020").mkdir()
    with pytest.raises(SourceDataError) as error:
        TDCCArchiveFetcher().fetch(
            LegacyTDCCArchiveAdapter(archive_root=tmp_path).resource(
                TDCCShareholdingRequest(date(2020, 1, 3))
            )
        )
    assert error.value.reason_code == "archive_file_missing"


def test_a_cut_landing_on_a_newline_is_still_reported_as_truncated() -> None:
    """The real 2023-10-20 file happens to be cut mid-row, which the byte
    check sees. A cut one byte later leaves no partial row at all, and the
    only remaining signal is the last security missing the rest of its
    levels."""
    text = TRUNCATED.decode("utf-8-sig")
    assert not text.endswith("\n")
    parsed = parse((text + "\r\n").encode("utf-8"), date(2023, 10, 20))
    assert parsed.truncated is True
    assert [item.security_code for item in parsed.rejected] == ["8162"]
    assert [row.security_code for row in parsed.rows] == ["2330"]


def test_a_complete_file_is_not_reported_as_truncated() -> None:
    for payload, week in ((LATEST, date(2026, 9, 18)), (SLASHED, date(2019, 6, 28))):
        assert parse(payload, week).truncated is False


def test_a_payload_cut_before_its_first_newline_says_so() -> None:
    with pytest.raises(SourceDataError) as error:
        parse(LATEST[:20])
    assert error.value.reason_code == "truncated_payload"


def test_a_holding_level_above_one_hundred_quarantines_that_security() -> None:
    text = LATEST.decode("utf-8-sig").replace(
        ",2496562,291447275,1.12", ",2496562,291447275,101.00", 1
    )
    parsed = parse(text.encode("utf-8"), date(2026, 9, 18))
    assert [item.security_code for item in parsed.rejected] == ["2330"]
    assert parsed.rejected[0].reason_code == "holding_level_above_one_hundred"
    assert [row.security_code for row in parsed.rows] == ["0056", "1101"]


def test_the_published_total_is_not_subject_to_that_ceiling() -> None:
    # Same value, on the total row: published as-is (audit §4.9).
    parsed = parse(DOUBLE_BOM, date(2020, 4, 30))
    assert [item.security_code for item in parsed.rejected] == []
