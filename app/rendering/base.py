"""Rendering contracts: `CertificateContent`, the `CertificateRenderer` protocol, `RenderError`.

Kept free of ReportLab/DB/FastAPI imports so any layer can depend on the shapes without pulling
in a concrete renderer (ARCHITECTURE §2, §10).
"""

from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class CertificateContent:
    """Everything a renderer needs to draw one certificate. Immutable: built once per render."""

    certificate_number: str
    recipient_name: str
    title: str
    program_name: str
    issuer_name: str
    issue_date: date
    signatory_name: str | None
    signatory_title: str | None


class CertificateRenderer(Protocol):
    """Anything that turns `CertificateContent` into PDF bytes.

    A protocol (not a base class) so the processor can depend on this shape without the
    renderer needing to inherit from anything (ARCHITECTURE §10).
    """

    def render(self, content: CertificateContent) -> bytes: ...


class RenderError(Exception):
    """Raised when rendering fails for any reason; the processor maps this to RENDER_ERROR.

    The message must be short and traceback-free (CLAUDE.md: error_message has no tracebacks) —
    the original exception is still chained via `raise ... from exc` for logs.
    """
