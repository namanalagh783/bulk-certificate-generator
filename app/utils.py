"""Small stateless helpers shared across services, workers and rendering.

Kept dependency-free (stdlib only) so they can be imported from any layer without pulling in
FastAPI, SQLAlchemy or ReportLab.
"""

import re
import secrets
import unicodedata
from datetime import UTC, datetime

# Uppercase alphabet with visually ambiguous characters removed (no 0/O, 1/I/L) so certificate
# numbers printed on a PDF and read aloud/typed by a human are unambiguous.
_CERT_NUMBER_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CERT_NUMBER_LENGTH = 10


def utcnow() -> datetime:
    """Timezone-aware current UTC time; the only clock the app should read (ARCHITECTURE §9)."""
    return datetime.now(UTC)


def new_certificate_number() -> str:
    """Generate a certificate number like 'CERT-7K2M9QX4PA'.

    Uses `secrets.choice` (not `random`) because this value is embedded in filenames and shown
    to recipients; it should not be guessable or predictable.
    """
    suffix = "".join(secrets.choice(_CERT_NUMBER_ALPHABET) for _ in range(_CERT_NUMBER_LENGTH))
    return f"CERT-{suffix}"


def slugify_filename(text: str, max_length: int = 60) -> str:
    """Turn arbitrary text into a safe, ASCII-only filename fragment.

    No user input ever appears unsanitised in a filesystem path (CLAUDE.md): this strips
    accents, drops everything but ASCII letters/digits, collapses separators to single hyphens,
    and never lets through "/", "\\" or ".." sequences used for path traversal.
    """
    # NFKD decomposes accented characters (e.g. "é" -> "e" + combining accent); encoding to
    # ASCII and ignoring errors then drops the accent marks and any other non-ASCII codepoints.
    normalised = unicodedata.normalize("NFKD", text)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    # Any run of non-alphanumeric characters (includes "/", "\\", ".", " ") becomes one hyphen.
    slug = re.sub(r"[^a-z0-9]+", "-", lowered)
    slug = slug.strip("-")
    slug = slug[:max_length].strip("-")
    return slug or "certificate"
