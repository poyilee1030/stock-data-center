"""Stable contracts shared by real-source ingestion components."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import Enum
from types import MappingProxyType
from urllib.parse import urlencode

from stock_data_center.institutional_financing.models import (
    InstitutionalInvestorObservation,
    InstitutionalMarketSummaryObservation,
)
from stock_data_center.provenance import ArtifactOrigin, IngestPurpose
from stock_data_center.market_data import (
    DailyPriceObservation,
    SecurityMetadataObservation,
)
from stock_data_center.market_reference.models import (
    MarketIndexObservation,
    OfficialValuationObservation,
)


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    """What a run declared, and when it actually fetched (ADR-0020 §5).

    Passed to the business writer so the evidence a version receives follows
    from the run that produced it, rather than from the clock at write time.
    """

    purpose: IngestPurpose
    captured_at: datetime


class SourceQuantityUnit(str, Enum):
    SHARE = "share"
    LOT_1000_SHARES = "lot_1000_shares"


class SourceMoneyUnit(str, Enum):
    TWD = "twd"
    THOUSAND_TWD = "thousand_twd"


@dataclass(frozen=True, slots=True)
class DailyMarketSourceSemantics:
    traded_quantity_unit: SourceQuantityUnit
    trade_value_unit: SourceMoneyUnit
    # Only the whole-market feeds publish a disclosed bid/ask level, so the
    # per-security pilots leave this undeclared rather than assuming one.
    disclosed_volume_unit: SourceQuantityUnit | None = None


@dataclass(frozen=True, slots=True)
class DailyMarketRequest:
    security_code: str
    month: date

    def __post_init__(self) -> None:
        if not self.security_code or self.security_code.strip() != self.security_code:
            raise ValueError("security_code must be nonempty and already trimmed")
        if self.month.day != 1:
            raise ValueError("month must be the first day of the requested month")


@dataclass(frozen=True, slots=True)
class WholeMarketDailyRequest:
    """Request one market's published closing quotes for one trade date."""

    trade_date: date


@dataclass(frozen=True, slots=True)
class WholeMarketDailyRow:
    """One security's quote inside a whole-market file."""

    security_code: str
    security_name: str
    observation: DailyPriceObservation


@dataclass(frozen=True, slots=True)
class ParsedWholeMarketDaily:
    """Every quote one market published for one trade date."""

    market: str
    trade_date: date
    rows: tuple[WholeMarketDailyRow, ...]
    source_fields: tuple[str, ...]
    header_variant: str

    @property
    def coverage_start(self) -> date | None:
        return self.trade_date if self.rows else None

    @property
    def coverage_end(self) -> date | None:
        return self.trade_date if self.rows else None


@dataclass(frozen=True, slots=True)
class MarketIndexRequest:
    """Request one market's published index closes for one trade date."""

    trade_date: date


@dataclass(frozen=True, slots=True)
class MarketIndexRow:
    """One index's close inside a whole-list index file.

    `section` is part of the identity, not decoration. TPEx publishes the same
    name in its price section and its return section — `櫃買指數` appears in
    both, at 395.52 and 735.15 — so the published name alone collides inside a
    single file. TWSE avoids it by naming return indices distinctly, but the
    identity has to hold for both feeds.
    """

    index_name: str
    section: str
    observation: MarketIndexObservation

    def index_code(self, source: str) -> str:
        return f"{source}:{self.section}:{self.index_name}"


@dataclass(frozen=True, slots=True)
class ParsedMarketIndex:
    """Every index one market published for one trade date."""

    market: str
    trade_date: date
    rows: tuple[MarketIndexRow, ...]
    section_count: int
    source_fields: tuple[str, ...]

    @property
    def coverage_start(self) -> date | None:
        return self.trade_date if self.rows else None

    @property
    def coverage_end(self) -> date | None:
        return self.trade_date if self.rows else None


@dataclass(frozen=True, slots=True)
class OfficialValuationRequest:
    """Request one market's published valuation ratios for one trade date."""

    trade_date: date


@dataclass(frozen=True, slots=True)
class OfficialValuationRow:
    security_code: str
    observation: OfficialValuationObservation


@dataclass(frozen=True, slots=True)
class RejectedValuationRow:
    """One row the source published but the contract cannot store.

    Its own quarantine, not the date's: a first-day `"0"` ratio for one
    security says nothing about the other 830 rows of that file.
    """

    row_number: int
    security_code: str
    reason_code: str
    detail: str


@dataclass(frozen=True, slots=True)
class ParsedOfficialValuation:
    """Every valuation one market published for one trade date."""

    market: str
    trade_date: date
    rows: tuple[OfficialValuationRow, ...]
    rejected: tuple[RejectedValuationRow, ...]
    header_variant: str
    source_fields: tuple[str, ...]

    @property
    def coverage_start(self) -> date | None:
        return self.trade_date if self.rows else None

    @property
    def coverage_end(self) -> date | None:
        return self.trade_date if self.rows else None


@dataclass(frozen=True, slots=True)
class InstitutionalInvestorRequest:
    """Request one market's per-security institutional flows for one trade date."""

    trade_date: date


@dataclass(frozen=True, slots=True)
class InstitutionalInvestorRow:
    security_code: str
    observation: InstitutionalInvestorObservation


@dataclass(frozen=True, slots=True)
class ParsedInstitutionalInvestor:
    """Every per-security flow one market published for one trade date."""

    market: str
    trade_date: date
    rows: tuple[InstitutionalInvestorRow, ...]
    header_variant: str
    source_fields: tuple[str, ...]

    @property
    def coverage_start(self) -> date | None:
        return self.trade_date if self.rows else None

    @property
    def coverage_end(self) -> date | None:
        return self.trade_date if self.rows else None


@dataclass(frozen=True, slots=True)
class InstitutionalMarketSummaryRequest:
    """Request one market's institutional trading-value summary for one trade date."""

    trade_date: date


@dataclass(frozen=True, slots=True)
class ParsedInstitutionalMarketSummary:
    """Every summary row one market published for one trade date, in order."""

    market: str
    trade_date: date
    rows: tuple[InstitutionalMarketSummaryObservation, ...]
    header_variant: str
    source_fields: tuple[str, ...]

    @property
    def coverage_start(self) -> date | None:
        return self.trade_date if self.rows else None

    @property
    def coverage_end(self) -> date | None:
        return self.trade_date if self.rows else None


@dataclass(frozen=True, slots=True)
class TaiexHistoryRequest:
    """Request one calendar month of TAIEX open/high/low/close."""

    month: date

    def __post_init__(self) -> None:
        if self.month.day != 1:
            raise ValueError("month must be the first day of the requested month")


@dataclass(frozen=True, slots=True)
class TaiexHistoryRow:
    trade_date: date
    observation: MarketIndexObservation


@dataclass(frozen=True, slots=True)
class ParsedTaiexHistory:
    """One month of OHLC for the single index this endpoint covers."""

    market: str
    index_name: str
    month: date
    rows: tuple[TaiexHistoryRow, ...]
    source_fields: tuple[str, ...]

    @property
    def coverage_start(self) -> date | None:
        return self.rows[0].trade_date if self.rows else None

    @property
    def coverage_end(self) -> date | None:
        return self.rows[-1].trade_date if self.rows else None


# Exchange result feeds (Invariant G(2)). Each row records an event the exchange
# executed and priced, so its locator is an identity. The announcement feeds are
# named only so that asking one for a locator fails loudly (ROADMAP §21.3).
RESULT_FEEDS = frozenset(
    {
        "TWT49U", "TWTAUU", "TWTB8U", "exDailyQ", "revivt", "pvChgRslt",
        "TWTCAU", "etfSplitRslt", "etfRvsRslt",
    }
)
ANNOUNCEMENT_FEEDS = frozenset(
    {"t187ap45_L", "mopsfin_t187ap39_O", "TWT48U", "t05st09sub"}
)


@dataclass(frozen=True, slots=True)
class ExchangeLocator:
    """The exchange's own address for one executed event.

    TWSE publishes it (`詳細資料`, e.g. `1101,20240701`); TPEx publishes none,
    so its executed date stands in. The key is the feed plus the locator's
    dates and nothing else: no amount, ratio, type or name can reach it.
    """

    feed: str
    security_code: str
    dates: tuple[date, ...]

    def __post_init__(self) -> None:
        if self.feed in ANNOUNCEMENT_FEEDS:
            raise SourceDataError(
                "announcement_feed",
                f"{self.feed} publishes plans, not executed events; it has no "
                f"correction-stable event identity (ROADMAP Invariant G(1))",
            )
        if self.feed not in RESULT_FEEDS:
            raise SourceDataError(
                "unknown_feed", f"{self.feed!r} is not a verified result feed"
            )
        if not self.security_code or self.security_code.strip() != self.security_code:
            raise ValueError("security_code must be nonempty and already trimmed")
        if not self.dates:
            raise ValueError("a locator names at least one date")

    @property
    def source_event_key(self) -> str:
        return f"{self.feed}:" + ",".join(
            value.strftime("%Y%m%d") for value in self.dates
        )


@dataclass(frozen=True, slots=True)
class CorporateActionRangeRequest:
    """One result feed over a date range, as TWSE and TPEx both serve it.

    `executed_through` is the last date whose rows count as executed. A
    current-year file already lists results for coming dates — fetched
    2026-09-16, TWT49U listed 2026-09-17 and TWTAUU 2026-10-19 — and those are
    not yet market facts. It is decided when the job is issued, never from the
    fetch clock, for the same reason the ingest purpose is (ROADMAP §3.1).
    """

    start: date
    end: date
    executed_through: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("start must not follow end")
        # A range with nothing executed yet would claim coverage ending before
        # it starts, and could only count rows; the issuer skips it instead.
        if self.executed_through < self.start:
            raise ValueError("executed_through must not precede start")


@dataclass(frozen=True, slots=True)
class CorporateActionDetailRequest:
    """The TWSE detail page that completes one list row."""

    locator: ExchangeLocator


@dataclass(frozen=True, slots=True)
class CorporateActionRow:
    """One executed event as its list row published it.

    `fields` holds what the row publishes, already in canonical units; the
    adapter's `observation` turns it into a storable version or refuses. A row
    with a `detail_request` is not an observation until that detail is read:
    TWSE's list does not publish the amounts.
    """

    security_code: str
    event_date: date
    locator: ExchangeLocator
    action_type: str
    source_event_type: str
    fields: Mapping[str, object]
    source_terms: Mapping[str, str]
    detail_request: CorporateActionDetailRequest | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))
        object.__setattr__(
            self, "source_terms", MappingProxyType(dict(self.source_terms))
        )


@dataclass(frozen=True, slots=True)
class ParsedCorporateActionList:
    """Every executed event one feed published for a range."""

    feed: str
    market: str
    start: date
    end: date
    executed_through: date
    rows: tuple[CorporateActionRow, ...]
    not_yet_executed: int
    source_fields: tuple[str, ...]

    @property
    def coverage_start(self) -> date:
        return self.start

    @property
    def coverage_end(self) -> date:
        """The range this file is complete for; a later row is not an event yet."""
        return min(self.end, self.executed_through)


@dataclass(frozen=True, slots=True)
class ParsedCorporateActionDetail:
    """One TWSE detail page, reduced to the terms its row needs.

    `values` holds decimals in the source's own per-share or per-thousand units;
    a published zero is None, because TWSE writes `0` where an item does not
    apply (`如果無該項配股率則用'0'帶入`).

    `locator` is the event the page was requested for. The page itself
    publishes a security code but no date, so only the request says which of
    that security's events it describes.
    """

    locator: ExchangeLocator
    security_code: str
    variant: str
    values: Mapping[str, object]
    source_terms: Mapping[str, str]
    source_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SecurityMetadataRequest:
    """Request the source's current official security-metadata snapshot."""

    expected_report_date: date | None = None


@dataclass(frozen=True, slots=True)
class TradingCalendarRequest:
    """Request one calendar month of actual trading days."""

    month: date

    def __post_init__(self) -> None:
        if self.month.day != 1:
            raise ValueError("month must be the first day of the requested month")


@dataclass(frozen=True, slots=True)
class ParsedTradingCalendar:
    """The actual trading days one source published for one month."""

    market: str
    month: date
    trading_days: tuple[date, ...]
    source_fields: tuple[str, ...]

    @property
    def coverage_start(self) -> date | None:
        return self.trading_days[0] if self.trading_days else None

    @property
    def coverage_end(self) -> date | None:
        return self.trading_days[-1] if self.trading_days else None


@dataclass(frozen=True, slots=True)
class SecurityLifecycleRequest:
    """Request one official historical listing-lifecycle resource."""

    year: int | None = None

    def __post_init__(self) -> None:
        if self.year is not None and not 1912 <= self.year <= 9999:
            raise ValueError("year must be a Gregorian year from 1912 through 9999")


@dataclass(frozen=True, slots=True)
class SecurityLifecycleEvent:
    security_code: str
    name: str
    effective_on: date
    event_kind: str
    market: str
    transfer_from_market: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedSecurityLifecycle:
    market: str
    event_kind: str
    rows: tuple[SecurityLifecycleEvent, ...]
    source_fields: tuple[str, ...]
    source_row_count: int

    @property
    def coverage_start(self) -> date | None:
        return self.rows[0].effective_on if self.rows else None

    @property
    def coverage_end(self) -> date | None:
        return self.rows[-1].effective_on if self.rows else None


@dataclass(frozen=True, slots=True)
class SecurityMetadataRecord:
    security_code: str
    observation: SecurityMetadataObservation


@dataclass(frozen=True, slots=True)
class ParsedSecurityMetadata:
    report_date: date
    market: str
    rows: tuple[SecurityMetadataRecord, ...]
    source_fields: tuple[str, ...]


_REQUEST_METHODS = frozenset({"GET", "POST"})

# Headers httpx derives from the URL and the body. A resource that set them
# could disagree with what is actually sent, and the stored request would then
# misdescribe the fetch.
_DERIVED_HEADERS = frozenset({"host", "content-length", "transfer-encoding"})

_SERIALIZED_FIELDS = frozenset(
    {"resource_key", "source_uri", "method", "body_base64", "headers"}
)


@dataclass(frozen=True, slots=True)
class SourceResource:
    """One fetch, completely described: what a job needs to replay it.

    `headers` are stored with lower-cased names, sorted, so two resources
    that send the same request compare equal and serialize identically.
    They are added to, and override, the fetcher's defaults. The body is
    bytes, sent as given: an adapter decides its encoding, as it decides
    the source's.
    """

    resource_key: str
    source_uri: str
    method: str = "GET"
    body: bytes | None = None
    headers: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.method not in _REQUEST_METHODS:
            raise ValueError(
                f"method must be one of {sorted(_REQUEST_METHODS)}, got {self.method!r}"
            )
        if self.body is not None and not isinstance(self.body, bytes):
            raise ValueError("body must be bytes")
        if self.method == "GET" and self.body is not None:
            raise ValueError("a GET request carries no body")
        normalized: dict[str, str] = {}
        for name, value in self.headers:
            key = name.strip().lower()
            if key in normalized:
                raise ValueError(f"duplicate header {key!r}")
            if key in _DERIVED_HEADERS:
                raise ValueError(f"header {key!r} is derived from the request")
            normalized[key] = value
        object.__setattr__(self, "headers", tuple(sorted(normalized.items())))

    @classmethod
    def form_post(
        cls,
        *,
        resource_key: str,
        source_uri: str,
        fields: Sequence[tuple[str, str]],
        headers: Sequence[tuple[str, str]] = (),
    ) -> SourceResource:
        """A POST of an ASCII `application/x-www-form-urlencoded` body, its
        fields in the given order."""
        return cls(
            resource_key=resource_key,
            source_uri=source_uri,
            method="POST",
            body=urlencode(list(fields)).encode("ascii"),
            headers=(
                *headers,
                ("content-type", "application/x-www-form-urlencoded"),
            ),
        )

    def to_json_object(self) -> dict[str, object]:
        return {
            "resource_key": self.resource_key,
            "source_uri": self.source_uri,
            "method": self.method,
            "body_base64": (
                None if self.body is None else base64.b64encode(self.body).decode("ascii")
            ),
            "headers": [list(pair) for pair in self.headers],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_json_object(), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_json_object(cls, data: Mapping[str, object]) -> SourceResource:
        unknown = set(data) - _SERIALIZED_FIELDS
        if unknown:
            raise ValueError(f"unknown SourceResource fields: {sorted(unknown)}")
        body = data["body_base64"]
        return cls(
            resource_key=str(data["resource_key"]),
            source_uri=str(data["source_uri"]),
            method=str(data["method"]),
            body=None if body is None else base64.b64decode(str(body), validate=True),
            headers=tuple(
                (str(name), str(value)) for name, value in data["headers"]  # type: ignore[union-attr]
            ),
        )

    @classmethod
    def from_json(cls, text: str) -> SourceResource:
        return cls.from_json_object(json.loads(text))

    def request_identity(self) -> dict[str, object] | None:
        """The serialized request when its URL alone does not identify it;
        `None` for a plain GET, whose URL an importer's scope already records."""
        if self.method == "GET" and self.body is None and not self.headers:
            return None
        return self.to_json_object()


@dataclass(frozen=True, slots=True)
class FetchedArtifact:
    content: bytes
    source_uri: str
    fetched_at: datetime
    media_type: str

    def __post_init__(self) -> None:
        if self.fetched_at.tzinfo is None or self.fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        object.__setattr__(self, "fetched_at", self.fetched_at.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class ParsedDailyMarket:
    security_code: str
    security_name: str
    requested_month: date
    rows: tuple[DailyPriceObservation, ...]
    source_fields: tuple[str, ...]

    @property
    def coverage_start(self) -> date | None:
        return self.rows[0].trade_date if self.rows else None

    @property
    def coverage_end(self) -> date | None:
        return self.rows[-1].trade_date if self.rows else None


@dataclass(frozen=True, slots=True)
class ResourceImportResult:
    resource_key: str
    source: str
    raw_artifact_hash: str | None
    raw_artifact_created: bool
    business_versions_created: int
    business_versions_deduplicated: int
    publication_evidence_created: int
    publication_evidence_deduplicated: int
    evidence_observations: int
    unknown_publication_observations: int
    normalized_rows: int
    coverage_start: date | None
    coverage_end: date | None
    resumed_from_checkpoint: bool = False


@dataclass(frozen=True, slots=True)
class ImportManifestResult:
    import_id: str
    status: str
    result_counts: Mapping[str, int]
    reconciliation: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "result_counts", MappingProxyType(dict(self.result_counts))
        )
        object.__setattr__(
            self, "reconciliation", MappingProxyType(dict(self.reconciliation))
        )


class SourceDataError(ValueError):
    """A source artifact cannot be mapped unambiguously to the contract."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(detail)
        self.reason_code = reason_code


class ResourceQuarantinedError(RuntimeError):
    """The raw artifact was retained but its normalized writes were rejected."""


class UnusableSourceResponseError(RuntimeError):
    """A dependency answered with content that is not a source answer at all
    (for example an HTML maintenance page), even after a live retry.

    Deliberately not a `SourceDataError`: it is an operational failure, not a
    row that cannot map to the contract, so it must leave the range resumable
    rather than quarantine one row and let the range finish `succeeded`."""

    def __init__(self, reason_code: str, resource_key: str, detail: str) -> None:
        super().__init__(f"{resource_key} unusable after retry ({reason_code}): {detail}")
        self.reason_code = reason_code
        self.resource_key = resource_key
