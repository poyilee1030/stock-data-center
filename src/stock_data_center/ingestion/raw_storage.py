"""Immutable content-addressed raw artifact storage."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StoredRawArtifact:
    digest: str
    storage_uri: str
    byte_size: int
    created: bool


class RawArtifactIntegrityError(RuntimeError):
    """Retained bytes are missing, outside the store, or fail hash validation."""


class LocalRawArtifactStore:
    """Store bytes by SHA-256 without overwriting an existing digest path."""

    def __init__(self, root: Path | str = Path("data/raw")) -> None:
        self._root = Path(root)

    def put(self, content: bytes) -> StoredRawArtifact:
        digest = sha256(content).hexdigest()
        destination = self._root / digest[:2] / digest
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            existing = destination.read_bytes()
            if sha256(existing).hexdigest() != digest or existing != content:
                raise RuntimeError(
                    "content-addressed raw artifact path contains different bytes"
                )
            return StoredRawArtifact(
                digest=digest,
                storage_uri=destination.as_posix(),
                byte_size=len(content),
                created=False,
            )

        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=destination.parent, prefix=f".{digest}.", delete=False
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name
            os.link(temporary_name, destination)
            created = True
        except FileExistsError:
            existing = destination.read_bytes()
            if sha256(existing).hexdigest() != digest or existing != content:
                raise RuntimeError(
                    "content-addressed raw artifact race stored different bytes"
                )
            created = False
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)

        return StoredRawArtifact(
            digest=digest,
            storage_uri=destination.as_posix(),
            byte_size=len(content),
            created=created,
        )

    def read(
        self,
        *,
        storage_uri: str,
        expected_digest: str,
        expected_byte_size: int,
    ) -> bytes:
        """Read retained bytes only after validating location, size, and SHA-256."""
        root = self._root.resolve()
        path = Path(storage_uri).resolve()
        try:
            path.relative_to(root)
        except ValueError as error:
            raise RawArtifactIntegrityError(
                "raw artifact URI is outside the configured store"
            ) from error
        try:
            content = path.read_bytes()
        except OSError as error:
            raise RawArtifactIntegrityError(
                f"retained raw artifact cannot be read: {error}"
            ) from error
        if len(content) != expected_byte_size:
            raise RawArtifactIntegrityError(
                "retained raw artifact byte size does not match PostgreSQL"
            )
        actual_digest = sha256(content).hexdigest()
        if actual_digest != expected_digest:
            raise RawArtifactIntegrityError(
                "retained raw artifact SHA-256 does not match PostgreSQL"
            )
        return content
