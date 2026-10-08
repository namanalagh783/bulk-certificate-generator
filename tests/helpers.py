"""Shared test helpers (not a test module itself). More helpers are added in later phases."""

from io import BytesIO

from pypdf import PdfReader

from app.rendering.base import CertificateContent, CertificateRenderer, RenderError
from app.storage.base import CertificateStorage, StorageError


def extract_pdf_text(data: bytes) -> str:
    """Extract all text from PDF bytes, concatenated across pages, for content assertions."""
    reader = PdfReader(BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


class FailingRenderer:
    """Delegates to a real renderer, except raising `RenderError` for named recipients."""

    def __init__(self, inner: CertificateRenderer, fail_for_names: set[str]) -> None:
        self._inner = inner
        self._fail_for_names = fail_for_names

    def render(self, content: CertificateContent) -> bytes:
        if content.recipient_name in self._fail_for_names:
            raise RenderError(f"forced failure for {content.recipient_name}")
        return self._inner.render(content)


class FailingStorage:
    """Delegates to a real storage backend, except raising `StorageError` for matching keys."""

    def __init__(self, inner: CertificateStorage, fail_for_keys_containing: str | set[str]) -> None:
        self._inner = inner
        needles = fail_for_keys_containing
        self._needles = {needles} if isinstance(needles, str) else set(needles)

    def save(self, key: str, data: bytes) -> int:
        if any(needle in key for needle in self._needles):
            raise StorageError(f"forced failure for {key}")
        return self._inner.save(key, data)

    def path_for(self, key: str):
        return self._inner.path_for(key)

    def exists(self, key: str) -> bool:
        return self._inner.exists(key)


class ManualDispatcher:
    """Records submitted job ids instead of running them; `run_all` processes them on demand."""

    def __init__(self) -> None:
        self.submitted: list[str] = []

    def submit(self, job_id: str) -> None:
        self.submitted.append(job_id)

    def run_all(self, processor) -> None:
        """Process every recorded job id with `processor.process`, then clear the list."""
        job_ids, self.submitted = self.submitted, []
        for job_id in job_ids:
            processor.process(job_id)

    def shutdown(self) -> None:
        """No background resources to release."""


def make_payload(n_valid: int = 3, invalid: list | None = None, **certificate_overrides) -> dict:
    """Build a POST /api/v1/certificate-jobs request body.

    Valid recipients are named "Recipient 1", "Recipient 2", ... with emails
    "recipient1@example.com", etc. `invalid` items (if any) are appended after the valid ones.
    """
    certificate = {
        "program_name": "Intro to Testing",
        "issuer_name": "Example Org",
        "issue_date": "2026-01-15",
        **certificate_overrides,
    }
    recipients = [
        {"name": f"Recipient {i}", "email": f"recipient{i}@example.com"}
        for i in range(1, n_valid + 1)
    ]
    if invalid:
        recipients.extend(invalid)
    return {"certificate": certificate, "recipients": recipients}
