"""The exchanges' industry reclassification announcements (Step 39-a, ADR-0030).

Each exchange announces a reclassification once a year, and a company's own
application when granted, as a numbered notice: its date (發文日期), its number
(發文字號), and in its text, company by company, 「由『A』改為『B』」 with the
day the new category takes effect (實施日期). One change is one
`industry_changes` row, keyed by (stock, source, effective date).

- TWSE: one list for every year (`announcement?keyword=產業類別`), a JSON detail
  per notice; source `twse_announcement`, the listed market.
- TPEx: a list per year (POST `bulletin/announcement`), a JSON detail per
  notice (POST `bulletin/annDetail`); source `tpex_announcement`, the OTC
  market. Emerging-board (興櫃) and pioneer-board (創櫃) changes are out of
  scope: notices only about them are skipped, and a joint notice's emerging-
  board paragraph is not read.
- The 2023 notices of both name only each company's new category in their text;
  its old one is in the PDF attached (a table per new category with 原產業別),
  whose codes must be exactly the text's.

Every name must be a known category that existed on its side of the effective
date (`stock_data_center.v2.industry`), or the notice is quarantined whole.
Notices amending the rules (要點, 審查準則) change no company and are counted.

Raw-first: every list, detail and attachment is one `fetches` row with its raw
file. A notice whose detail was once parsed and written is not asked again
unless `refetch`; its rows are then compared, and only a changed value is a new
row. A key already held by another notice is refused, never overwritten, so
what is stored does not depend on the order notices were read. A refetched
notice that no longer lists a key it wrote (its effective date corrected, a
company dropped) is quarantined: nothing retracts a row, so the owner decides.
A page any parser cannot read is quarantined alone, never the whole run.
"""

from __future__ import annotations

import base64
import functools
import html as html_lib
import io
import json
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, quote, urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy import Connection

from stock_data_center.v2 import industry

ADAPTER_VERSION = "industry_changes:v1"
DATASET = "industry_changes"
SOURCES = {"twse_announcement": "sii", "tpex_announcement": "otc"}
PAUSE_SECONDS = 3.0
FIRST_TPEX_YEAR = 2019  # the first notice taking effect in the window is 2020's
TAIPEI = ZoneInfo("Asia/Taipei")

TWSE_HOST = "https://www.twse.com.tw"
TWSE_LIST_URL = (f"{TWSE_HOST}/rwd/zh/announcement/announcement?keyword={quote('產業類別')}"
                 "&response=json")
TWSE_DETAIL_URL = f"{TWSE_HOST}/rwd/zh/announcement/announcement_detail?id={{id}}&response=json"
TPEX_HOST = "https://www.tpex.org.tw"
TPEX_LIST_URL = f"{TPEX_HOST}/www/zh-tw/bulletin/announcement"
TPEX_DETAIL_URL = f"{TPEX_HOST}/www/zh-tw/bulletin/annDetail"

TWSE_LIST_FIELDS = ["項次", "發文日期", "發文字號", "主旨", "id"]
TWSE_DETAIL_FIELDS = ["發文機關", "發文日期", "發文字號", "主旨", "依據", "公告事項"]
TPEX_LIST_FIELDS = ["項次", "資料日期", "發文字號", "主旨", "詳細資料"]
# The attachment tables' market titles, and a row's remark column.
ATTACHMENT_TITLES = {"上市公司產業類別調整名單": "sii", "上櫃公司產業類別調整名單": "otc",
                     "興櫃公司產業類別調整名單": "emerging"}
REMARKS = frozenset({"第一上市", "創新版", "創新板"})
# The attachments' lines that are neither rows nor a wrapped cell: each table's
# header and the closing note (「上開公司之證券代號不予變更…」).
NOT_A_ROW = ("序號", "上開公司")


class AnnouncementFormatError(ValueError):
    """A notice this parser cannot read, or one whose content cannot be true.

    The message starts with the reason code the fetch is quarantined under."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Listed:
    """One row of a list: a notice and where its detail is."""

    source: str
    key: str  # the document number's digits
    announced_on: date
    document_number: str
    subject: str
    locator: dict[str, str]


@dataclass(frozen=True, slots=True)
class Announcement:
    source: str
    key: str
    document_number: str
    announced_on: date
    effective_date: date
    subject: str
    text: str
    attachments: list[tuple[str, str]] = field(default_factory=list)  # (title, url)


@dataclass(frozen=True, slots=True)
class Change:
    stock_id: str
    old_industry: str
    new_industry: str


# ---------------------------------------------------------------- parsing


def _payload(content: bytes) -> dict:
    try:
        payload = json.loads(content)
    except ValueError as error:
        raise AnnouncementFormatError("unrecognised_layout", f"not JSON: {error}") from None
    if not isinstance(payload, dict) or payload.get("stat") != "ok":
        raise AnnouncementFormatError("unrecognised_layout", "not an answer with stat ok")
    return payload


def _roc(text: str) -> date:
    match = re.fullmatch(r"(?:中華民國)?\s*(\d{2,3})\s*[年/]\s*(\d{1,2})\s*[月/]\s*(\d{1,2})\s*日?",
                         text.strip())
    if not match:
        raise AnnouncementFormatError("unrecognised_layout", f"not a ROC date: {text!r}")
    year, month, day = (int(part) for part in match.groups())
    return date(year + 1911, month, day)


def _key(document_number: str) -> str:
    digits = "".join(re.findall(r"\d+", document_number))
    if not digits:
        raise AnnouncementFormatError("unrecognised_layout", f"no number in {document_number!r}")
    return digits


def _readable(parse):
    """A parser whose every failure is a format error, so it quarantines one page
    instead of rolling back the whole run (code review of #72)."""

    @functools.wraps(parse)
    def wrapped(*args, **kwargs):
        try:
            return parse(*args, **kwargs)
        except AnnouncementFormatError:
            raise
        # Anything parsing an outside page raises is a page this code does not
        # understand; ValueError also covers JSON, base64 and Unicode errors.
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as error:
            raise AnnouncementFormatError(
                "unrecognised_layout", f"{type(error).__name__}: {error}"[:300]) from None

    return wrapped


@_readable
def parse_twse_list(content: bytes) -> list[Listed]:
    payload = _payload(content)
    if payload.get("fields") != TWSE_LIST_FIELDS:
        raise AnnouncementFormatError("unrecognised_layout",
                                      f"TWSE list fields changed: {payload.get('fields')!r}")
    rows = payload.get("data") or []
    if payload.get("total") != len(rows):
        raise AnnouncementFormatError("unrecognised_layout",
                                      f"TWSE list has {len(rows)} of {payload.get('total')} rows")
    return [Listed("twse_announcement", _key(row[2]), _roc(row[1]), row[2].strip(),
                   row[3].strip(), {"id": row[4].strip()}) for row in rows]


@_readable
def parse_tpex_list(content: bytes, year: int) -> list[Listed]:
    payload = _payload(content)
    if payload.get("date") != f"{year}0101~{year}1231":
        raise AnnouncementFormatError("unrecognised_layout",
                                      f"TPEx list answered {payload.get('date')!r} for {year}")
    table = (payload.get("tables") or [{}])[0]
    if table.get("fields") != TPEX_LIST_FIELDS:
        raise AnnouncementFormatError("unrecognised_layout",
                                      f"TPEx list fields changed: {table.get('fields')!r}")
    rows = table.get("data") or []
    if table.get("totalCount") != len(rows):
        raise AnnouncementFormatError("unrecognised_layout",
                                      f"TPEx list has {len(rows)} of {table.get('totalCount')} rows")
    listed = []
    for row in rows:
        query = parse_qs(urlsplit(row[4]).query)
        locator = {"content_file": query["content_file"][0], "docId": query["docId"][0]}
        key = _key(row[2])
        if base64.b64decode(locator["docId"], validate=True).decode() != key:
            raise AnnouncementFormatError("unrecognised_layout",
                                          f"TPEx docId does not name {row[2]!r}")
        listed.append(Listed("tpex_announcement", key, _roc(row[1]), row[2].strip(),
                             row[3].strip(), locator))
    return listed


def classify(subject: str) -> str:
    """`change`, `regulation` (the rules amended) or `out_of_scope` (another board)."""
    if "要點" in subject or "準則" in subject:
        return "regulation"
    if "創櫃" in subject or ("興櫃" in subject and "上櫃" not in subject
                             and "上（興）櫃" not in subject):
        return "out_of_scope"
    return "change"


_EFFECTIVE = (re.compile(r"實施日期[：:]\s*(\d{2,3})年(\d{1,2})月(\d{1,2})日"),
              re.compile(r"(?:自|於)(\d{2,3})年(\d{1,2})月(\d{1,2})日起實施"))


def _effective_date(*texts: str) -> date:
    found = {date(int(y) + 1911, int(m), int(d))
             for text in texts for pattern in _EFFECTIVE for y, m, d in pattern.findall(text)}
    if len(found) != 1:
        raise AnnouncementFormatError("effective_date", f"found {sorted(found)!r}")
    return found.pop()


@_readable
def parse_twse_detail(content: bytes) -> Announcement:
    payload = _payload(content)
    fields = payload.get("fields") or []
    if fields[:6] != TWSE_DETAIL_FIELDS or fields[6:] not in ([], ["相關附件"]):
        raise AnnouncementFormatError("unrecognised_layout", f"TWSE detail fields: {fields!r}")
    rows = payload.get("data") or []
    if len(rows) != 1:
        raise AnnouncementFormatError("unrecognised_layout", f"TWSE detail has {len(rows)} rows")
    row = rows[0]
    attachments = [(title, TWSE_HOST + path) for title, path in json.loads(row[6])] \
        if len(row) > 6 and row[6] else []
    subject, text = row[3].strip(), row[5]
    return Announcement("twse_announcement", _key(row[2]), row[2].strip(), _roc(row[1]),
                        _effective_date(subject, text), subject, text, attachments)


def _html_text(content: str) -> str:
    content = re.sub(r"(?i)<br\s*/?>|</p>", "\n", content)
    return html_lib.unescape(re.sub(r"<[^>]+>", "", content)).strip()


@_readable
def parse_tpex_detail(content: bytes) -> Announcement:
    payload = _payload(content)
    data = payload.get("data")
    if not isinstance(data, dict) or not {"date", "number", "subject", "content"} <= set(data):
        raise AnnouncementFormatError("unrecognised_layout", "TPEx detail without its fields")
    subject, text = data["subject"].strip(), _html_text(data["content"])
    attachments = [(item["title"], TPEX_HOST + item["url"]) for item in data.get("files") or []]
    return Announcement("tpex_announcement", _key(data["number"]), data["number"].strip(),
                        _roc(data["date"]), _effective_date(subject, text), subject, text,
                        attachments)


_CHANGE = re.compile(
    r"代號[：:\s]*([0-9]{4}[0-9A-Z]{0,2})\s*[)）]?\s*由「([^「」]+)」+\s*(?:改為|調整為)「([^「」]+)」")
_CODE = re.compile(r"[(（]([0-9]{4}[0-9A-Z]{0,2})[)）]")
_PARAGRAPH = re.compile(r"^[一二三四五六七八九十]+、", re.MULTILINE)


def scoped_text(text: str, market: str) -> str:
    """The text without the paragraphs about emerging-board companies."""
    if market != "otc":
        return text
    starts = [match.start() for match in _PARAGRAPH.finditer(text)] or [0]
    paragraphs = [text[:starts[0]]] + [text[a:b] for a, b in zip(starts, starts[1:] + [None])]
    return "".join(p for p in paragraphs if "興櫃公司" not in p.split("\n", 1)[0])


def text_changes(text: str) -> list[Change]:
    """Each 「代號 X 由『A』改為『B』」 in a notice's text, in order."""
    return [Change(code, old.strip(), new.strip()) for code, old, new in _CHANGE.findall(text)]


def text_codes(text: str, market: str) -> set[str]:
    """Every stock code the text names in brackets, as the 2023 notices list them."""
    return set(_CODE.findall(scoped_text(text, market)))


_SECTION = re.compile(r"調整至「(.+?)」[：:]\s*共計\s*(\d+)\s*家")
_ROW = re.compile(r"^(\d+)\s+(?:(上市|上櫃|興櫃\S*)\s+)?([0-9]{4}[0-9A-Z]{0,2})\s+(.+)$")


def parse_attachment(content: bytes, market: str) -> list[Change]:
    """An attachment's changes, if its table is `market`'s; [] if another board's.

    A row's last cell is its old category, which the PDF may wrap onto the
    lines after it; the joined cell must then be a known category."""
    from pypdf import PdfReader

    try:
        pages = [page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages]
    except Exception as error:  # noqa: BLE001 - any failure is a document this cannot read
        raise AnnouncementFormatError("unreadable_attachment", str(error)[:200]) from None
    lines = [line.strip() for page in pages for line in page.splitlines()]
    titles = {ATTACHMENT_TITLES[t] for t in ATTACHMENT_TITLES for line in lines if t in line}
    if len(titles) != 1:
        raise AnnouncementFormatError("unrecognised_attachment", f"titles {sorted(titles)!r}")
    if titles.pop() != market:
        return []
    sections: list[tuple[str, int, list[list[str]]]] = []
    entry: list[str] | None = None  # the last row, whose old-category cell may go on
    for line in lines:
        if (section := _SECTION.search(line)) is not None:
            _unwrapped(entry)
            entry = None
            sections.append((section.group(1), int(section.group(2)), []))
            continue
        if (row := _ROW.match(line)) is not None and sections:
            _unwrapped(entry)
            number, board, code, rest = row.groups()
            if board is not None and board != "上櫃":
                raise AnnouncementFormatError("unrecognised_attachment", f"{code} is on {board}")
            cells = rest.split()
            if len(cells) > 2 and cells[-1] in REMARKS:
                cells.pop()
            entry = [code, cells[-1], number]
            sections[-1][2].append(entry)
            continue
        if (not line or line.isdigit() or line.startswith(NOT_A_ROW)
                or any(title in line for title in ATTACHMENT_TITLES)):
            continue  # page numbers, the title, table headers and the closing note
        # Anything else continues the last row's old category, which the PDF
        # wraps: 「電腦及週邊」+「設備業」, or 「其他」+「電子業」, where the
        # first line alone is a category too. Text that continues no category
        # is not understood, and is never dropped (code review of #72).
        joined = entry[1] + line if entry is not None else line
        if entry is None or not industry.begins_a_name(joined):
            raise AnnouncementFormatError("unrecognised_attachment", f"stray line {line!r}")
        entry[1] = joined
    _unwrapped(entry)
    changes = []
    for new, declared, rows in sections:
        if [int(number) for _, _, number in rows] != list(range(1, declared + 1)):
            raise AnnouncementFormatError("unrecognised_attachment",
                                          f"「{new}」 declares {declared} rows, has {len(rows)}")
        changes += [Change(code, old, new) for code, old, _ in rows]
    if not changes:
        raise AnnouncementFormatError("unrecognised_attachment", "no rows")
    return changes


def _unwrapped(entry) -> None:
    if entry is not None and industry.code_of(entry[1]) is None:
        raise AnnouncementFormatError("unknown_industry", f"{entry[0]}: {entry[1]!r}")


def validate(notice: Announcement, changes: list[Change]) -> list[Change]:
    """The changes, if every one can be true on the notice's effective date."""
    if not changes:
        raise AnnouncementFormatError("no_changes", notice.document_number)
    market = SOURCES[notice.source]
    counts = Counter(change.stock_id for change in changes)
    if twice := sorted(code for code, n in counts.items() if n > 1):
        raise AnnouncementFormatError("duplicate_stock", ", ".join(twice))
    day_before = notice.effective_date - timedelta(days=1)
    for change in changes:
        old, new = industry.code_of(change.old_industry), industry.code_of(change.new_industry)
        if old is None or new is None:
            raise AnnouncementFormatError(
                "unknown_industry", f"{change.stock_id}: {change.old_industry!r} -> "
                                    f"{change.new_industry!r}")
        if old == new:
            raise AnnouncementFormatError("no_change", change.stock_id)
        if not (industry.exists(old, market, day_before)
                and industry.exists(new, market, notice.effective_date)):
            raise AnnouncementFormatError(
                "industry_not_in_effect", f"{change.stock_id}: {change.old_industry} -> "
                                          f"{change.new_industry} on {notice.effective_date}")
    return changes


# ---------------------------------------------------------------- ingestion


def _latest(connection: Connection, source: str) -> dict[tuple, dict]:
    from stock_data_center.db.schema_v2 import industry_changes as table

    rows = connection.execute(
        sa.select(table).where(table.c.source == source)
        .order_by(table.c.stock_id, table.c.effective_date, table.c.recorded_at.desc())
        .distinct(table.c.stock_id, table.c.effective_date)
    ).mappings()
    return {(row["stock_id"], row["effective_date"]): dict(row) for row in rows}


VALUES = ("announced_on", "document_number", "old_industry", "new_industry")


def _lock(connection: Connection, source: str) -> None:
    connection.execute(sa.select(sa.func.pg_advisory_xact_lock(
        sa.func.hashtext(f"{DATASET}/{source}"))))


def withdrawn(connection: Connection, source: str, document_number: str,
              rows: list[dict]) -> list[tuple[str, date]]:
    """The keys this notice held that its refetched text no longer lists.

    A notice whose effective date was corrected, or that dropped a company,
    would leave its old rows standing: there is no retraction, so such a notice
    is quarantined for the owner instead (code review of #72). A company that
    joined `stocks` since is simply added."""
    _lock(connection, source)
    listed = {(row["stock_id"], row["effective_date"]) for row in rows}
    return sorted(key for key, old in _latest(connection, source).items()
                  if old["document_number"] == document_number and key not in listed)


def write(connection: Connection, source: str, rows: list[dict]) -> Counter:
    """Append each row whose values differ from its key's latest; refuse a key
    another notice already holds."""
    from stock_data_center.db.schema_v2 import industry_changes as table

    _lock(connection, source)
    stored = _latest(connection, source)
    counts: Counter = Counter(appended=0, unchanged=0, conflicts=0)
    fresh = []
    for row in rows:
        old = stored.get((row["stock_id"], row["effective_date"]))
        if old is not None and old["document_number"] != row["document_number"]:
            counts["conflicts"] += 1
            continue
        if old is not None and all(old[c] == row[c] for c in VALUES):
            counts["unchanged"] += 1
            continue
        fresh.append({"source": source, **row})
    if fresh:
        connection.execute(sa.insert(table), fresh)
    counts["appended"] += len(fresh)
    return counts


def _done(connection: Connection, source: str, resource_key: str) -> bool:
    from stock_data_center.db.schema_v2 import fetches as f

    status = connection.scalar(
        sa.select(f.c.status).where(f.c.dataset == DATASET, f.c.source == source,
                                    f.c.resource_key == resource_key)
        .order_by(f.c.fetched_at.desc(), f.c.attempt.desc()).limit(1))
    return status == "succeeded"


Http = Callable[[str, str, dict | None], bytes]


def _http(method: str, url: str, form: dict | None) -> bytes:
    import httpx

    response = httpx.request(method, url, data=form, timeout=180)
    return response.raise_for_status().content


def ingest(connection: Connection, source: str, *, git_commit: str, http: Http | None = None,
           now: Callable[[], datetime] | None = None, store=None,
           pause: Callable[[], None] | None = None, purpose: str = "gap_fill",
           refetch: bool = False) -> dict:
    """Fetch one exchange's notices raw-first and write their changes, in the
    caller's transaction. The result counts every notice by what became of it."""
    from stock_data_center.db.schema_v2 import stocks
    from stock_data_center.ingestion.raw_storage import LocalRawArtifactStore
    from stock_data_center.v2.fetch_log import FetchRecord, record_fetch

    http = http or _http
    now = now or (lambda: datetime.now(UTC))
    store = store or LocalRawArtifactStore()
    pause = pause or (lambda: time.sleep(PAUSE_SECONDS))
    market = SOURCES[source]
    day = now().astimezone(TAIPEI).date()
    report: Counter = Counter()
    quarantined, outside, conflicts = [], set(), []
    asked = 0

    def get(resource_key: str, method: str, url: str, form: dict | None = None):
        """(record, content), or (record, None) after logging the failure."""
        nonlocal asked
        if asked:
            pause()
        asked += 1
        record = FetchRecord(DATASET, source, resource_key, url, purpose, ADAPTER_VERSION,
                             git_commit, now())
        try:
            return record, http(method, url, form)
        except Exception as error:  # noqa: BLE001 - every failure is logged
            record_fetch(connection, record, content=None, status="failed",
                         reason_code="request_failed", reason_detail=str(error)[:500])
            report["failed"] += 1
            return record, None

    def log(record, content, status="succeeded", reason=None, detail=None) -> UUID:
        return record_fetch(connection, record, content=content, status=status, store=store,
                            reason_code=reason, reason_detail=detail)

    # The lists.
    listed: list[Listed] = []
    if source == "twse_announcement":
        pages = [(f"{source}:list:{day}", "GET", TWSE_LIST_URL, None, parse_twse_list)]
    else:
        pages = [(f"{source}:list:{year}:{day}", "POST", TPEX_LIST_URL,
                  {"startDate": f"{year}/01/01", "endDate": f"{year}/12/31",
                   "txtKeyword": "產業類別", "response": "json"},
                  lambda c, y=year: parse_tpex_list(c, y))
                 for year in range(FIRST_TPEX_YEAR, day.year + 1)]
    for resource_key, method, url, form, parse in pages:
        record, content = get(resource_key, method, url, form)
        if content is None:
            return {"written": False, "reason": f"{resource_key}: request failed",
                    "requests": asked}
        try:
            listed += parse(content)
        except AnnouncementFormatError as error:
            log(record, content, "quarantined", error.reason, str(error)[:500])
            return {"written": False, "reason": f"{resource_key}: {error}", "requests": asked}
        log(record, content)

    universe = set(connection.scalars(sa.select(stocks.c.stock_id)))
    for notice in sorted(listed, key=lambda item: (item.announced_on, item.key)):
        kind = classify(notice.subject)
        report[kind] += 1
        if kind != "change":
            continue
        resource_key = f"{source}:{notice.key}"
        if not refetch and _done(connection, source, resource_key):
            report["skipped"] += 1
            continue
        if source == "twse_announcement":
            record, content = get(resource_key, "GET",
                                  TWSE_DETAIL_URL.format(id=notice.locator["id"]))
        else:
            record, content = get(resource_key, "POST", TPEX_DETAIL_URL,
                                  {**notice.locator, "response": "json"})
        if content is None:
            continue
        try:
            parsed = (parse_twse_detail if source == "twse_announcement"
                      else parse_tpex_detail)(content)
            if parsed.document_number != notice.document_number:
                raise AnnouncementFormatError("detail_mismatch", parsed.document_number)
            if parsed.effective_date < industry.WINDOW_START:
                log(record, content, reason="before_window")
                report["before_window"] += 1
                continue
            scoped = scoped_text(parsed.text, market)
            changes = text_changes(scoped)
            sheet: dict[str, UUID] = {}  # stock -> the attachment its row came from
            if not changes and parsed.attachments:
                for number, (_title, url) in enumerate(parsed.attachments, 1):
                    attachment, pdf = get(f"{resource_key}:attachment:{number}", "GET", url)
                    if pdf is None:
                        raise AnnouncementFormatError("attachment_failed", url)
                    try:
                        rows = parse_attachment(pdf, market)
                    except AnnouncementFormatError as error:
                        log(attachment, pdf, "quarantined", error.reason, str(error)[:500])
                        raise
                    fetch_id = log(attachment, pdf, reason=None if rows else "another_board")
                    changes += rows
                    sheet.update({row.stock_id: fetch_id for row in rows})
                if {change.stock_id for change in changes} != text_codes(scoped, market):
                    raise AnnouncementFormatError("attachment_disagrees",
                                                  "its codes are not the text's")
            validate(parsed, changes)
        except AnnouncementFormatError as error:
            log(record, content, "quarantined", error.reason, str(error)[:500])
            quarantined.append((notice.key, error.reason))
            continue
        rows = []
        for change in changes:
            if change.stock_id not in universe:
                outside.add(change.stock_id)
                continue
            rows.append({"stock_id": change.stock_id, "effective_date": parsed.effective_date,
                         "announced_on": parsed.announced_on,
                         "document_number": parsed.document_number,
                         "old_industry": change.old_industry, "new_industry": change.new_industry,
                         "attachment_fetch_id": sheet.get(change.stock_id)})
        if gone := withdrawn(connection, source, parsed.document_number, rows):
            detail = ", ".join(f"{stock} {day}" for stock, day in gone)
            log(record, content, "quarantined", "notice_changed",
                f"no longer lists {detail}"[:500])
            quarantined.append((notice.key, "notice_changed"))
            continue
        fetch_id = log(record, content)
        report["notices"] += 1
        report["changes"] += len(changes)
        for row in rows:
            row["fetch_id"] = fetch_id
        counts = write(connection, source, rows)
        report.update(counts)
        if counts["conflicts"]:
            conflicts.append(notice.key)
    return {"written": True, "requests": asked, **dict(report),
            "quarantined": quarantined, "outside_universe": sorted(outside),
            "conflicting_notices": conflicts}


def main(argv: list[str] | None = None) -> int:
    """`DATABASE_URL=... python -m stock_data_center.v2.industry_changes [--refetch]`:
    each exchange in its own transaction."""
    import argparse
    import os

    from stock_data_center.v2.fetch_log import current_git_commit

    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=sorted(SOURCES), action="append")
    parser.add_argument("--purpose", default="gap_fill",
                        choices=["gap_fill", "correction_check", "first_capture", "unspecified"])
    parser.add_argument("--refetch", action="store_true")
    args = parser.parse_args(argv)
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    engine = sa.create_engine(url)
    commit = current_git_commit()
    reports = {}
    try:
        for source in args.source or sorted(SOURCES):
            with engine.begin() as connection:
                reports[source] = ingest(connection, source, git_commit=commit,
                                         purpose=args.purpose, refetch=args.refetch)
    finally:
        engine.dispose()
    print(json.dumps(reports, ensure_ascii=False, default=str, indent=2))
    return 0 if all(report["written"] and not report["quarantined"]
                    for report in reports.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
