"""Storage contracts: `CertificateStorage` protocol, `StorageError`, the file key convention.

Kept free of DB/FastAPI imports so any layer can depend on the shape without pulling in a
concrete storage backend (ARCHITECTURE §2, §10).
"""

from pathlib import Path
from typing import Protocol


class CertificateStorage(Protocol):
    """Anything that can save/locate certificate files by key."""

    def save(self, key: str, data: bytes) -> int:
        """Persist `data` under `key`; returns the number of bytes written."""
        ...

    def path_for(self, key: str) -> Path:
        """Resolve `key` to a filesystem path (does not require the file to exist)."""
        ...

    def exists(self, key: str) -> bool:
        """Whether a file currently exists at `key`."""
        ...


class StorageError(Exception):
    """Raised when a storage operation fails (invalid key or an underlying OS error)."""


def certificate_key(job_id: str, certificate_id: str) -> str:
    """Build the deterministic storage key for one certificate (ADR-007).

    `job_id`/`certificate_id` are always server-generated UUIDs (never user input), so this
    string can never contain path-traversal characters (CLAUDE.md: no user input in paths).
    """
    return f"certificates/{job_id}/{certificate_id}.pdf"
