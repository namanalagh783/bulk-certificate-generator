"""LocalFileStorage: certificate files on local disk, written atomically (ADR-007).

Every public method resolves the key through `_resolve`, which rejects anything that could
escape `root` before touching the filesystem — defence in depth even though, in practice, keys
are always built by `certificate_key()` from server-generated UUIDs.
"""

import contextlib
import os
import tempfile
from pathlib import Path, PurePosixPath

from app.storage.base import StorageError


class LocalFileStorage:
    """Stores certificate files under `root`, resolving/validating every key first."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def _resolve(self, key: str) -> Path:
        """Validate `key` and resolve it to an absolute path inside `root`.

        Rejects: empty keys, backslashes (so a Windows-style path can't sneak in on any OS),
        a leading "/" or a drive letter (e.g. "C:"), and any ".." segment. As a final defence,
        the resolved path must still be inside `root.resolve()`.
        """
        if not key:
            raise StorageError("storage key must not be empty")
        if "\\" in key:
            raise StorageError(f"storage key must not contain backslashes: {key!r}")
        if key.startswith("/") or (len(key) >= 2 and key[1] == ":"):
            raise StorageError(f"storage key must be relative: {key!r}")
        if ".." in PurePosixPath(key).parts:
            raise StorageError(f"storage key must not contain '..': {key!r}")

        root = self._root.resolve()
        resolved = (root / key).resolve()
        if resolved != root and root not in resolved.parents:
            raise StorageError(f"storage key escapes storage root: {key!r}")
        return resolved

    def path_for(self, key: str) -> Path:
        """Resolve `key` to a filesystem path (the file may or may not exist yet)."""
        return self._resolve(key)

    def exists(self, key: str) -> bool:
        """Whether a file currently exists at `key`."""
        return self._resolve(key).is_file()

    def save(self, key: str, data: bytes) -> int:
        """Write `data` to `key` atomically: temp file in the same directory, then `os.replace`.

        Writing to a sibling temp file and renaming into place means readers never see a
        partially written file, and a crash mid-write leaves only a stray temp file, never a
        corrupt final one.
        """
        final_path = self._resolve(key)
        final_path.parent.mkdir(parents=True, exist_ok=True)

        tmp_path: str | None = None
        try:
            fd, tmp_path = tempfile.mkstemp(dir=final_path.parent, prefix=".tmp-", suffix=".part")
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, final_path)
            tmp_path = None  # successfully moved; nothing left to clean up
            return len(data)
        except OSError as exc:
            raise StorageError(f"failed to save certificate file: {exc}") from exc
        finally:
            if tmp_path is not None:
                with contextlib.suppress(OSError):
                    os.remove(tmp_path)
