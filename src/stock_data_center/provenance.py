"""Shared provenance vocabulary.

`IngestPurpose` governs which evidence an import may claim (ADR-0020 §5) and
`ArtifactOrigin` records how its bytes were obtained (ROADMAP §14). Both are
needed by the ingestion lifecycle *and* by the evidence policy that reads what
the lifecycle declared, so they live outside either package rather than making
the two import each other.
"""

from __future__ import annotations

from enum import Enum


class IngestPurpose(str, Enum):
    """Why a fetch was requested.

    Declared when the fetch is requested, never inferred afterwards: a row
    fetched years later because a query noticed it was missing must not be able
    to claim a capture bound at that later instant.
    """

    FIRST_CAPTURE = "first_capture"
    GAP_FILL = "gap_fill"
    CORRECTION_CHECK = "correction_check"
    # Runs that predate the policy, and any run that declines to declare.
    UNSPECIFIED = "unspecified"


class ArtifactOrigin(str, Enum):
    """How the bytes were obtained (ROADMAP §14)."""

    OFFICIAL_FETCH = "official_fetch"
    LEGACY_ARCHIVE = "legacy_archive"
