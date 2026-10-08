"""AppError hierarchy mapping domain errors to HTTP status codes (added in Phase 2).

Kept free of FastAPI imports (ARCHITECTURE §2): these are plain exceptions raised by services
and translated to the uniform error envelope by `app/api/error_handlers.py`.
"""

from typing import Any


class AppError(Exception):
    """Base class for all domain errors that map to a specific HTTP response.

    Subclasses fix `code` and `http_status` so call sites only supply the data needed for the
    human-readable `message` and machine-readable `details` (API_SPEC.md error envelope).
    """

    code: str = "INTERNAL_ERROR"
    http_status: int = 500

    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class JobNotFound(AppError):
    """Unknown job id (404)."""

    code = "JOB_NOT_FOUND"
    http_status = 404

    def __init__(self, job_id: str) -> None:
        super().__init__(f"Job {job_id} not found")


class CertificateNotFound(AppError):
    """Unknown certificate id (404)."""

    code = "CERTIFICATE_NOT_FOUND"
    http_status = 404

    def __init__(self, certificate_id: str) -> None:
        super().__init__(f"Certificate {certificate_id} not found")


class CertificateNotReady(AppError):
    """Download requested for a certificate that has not succeeded yet (409)."""

    code = "CERTIFICATE_NOT_READY"
    http_status = 409

    def __init__(self, status: str) -> None:
        super().__init__(
            f"Certificate is not ready for download (status: {status})",
            details={"status": status},
        )


class JobNotFinished(AppError):
    """ZIP requested for a job that has not reached a terminal status (409)."""

    code = "JOB_NOT_FINISHED"
    http_status = 409

    def __init__(self, status: str) -> None:
        super().__init__(
            f"Job has not finished processing (status: {status})",
            details={"status": status},
        )


class NoCertificatesAvailable(AppError):
    """ZIP requested but zero certificates succeeded (409)."""

    code = "NO_CERTIFICATES_AVAILABLE"
    http_status = 409

    def __init__(self, job_id: str) -> None:
        super().__init__(f"Job {job_id} has no succeeded certificates to download")


class TooManyRecipients(AppError):
    """More recipients submitted than `MAX_RECIPIENTS_PER_JOB` (422)."""

    code = "TOO_MANY_RECIPIENTS"
    http_status = 422

    def __init__(self, max_allowed: int, received: int) -> None:
        super().__init__(
            f"Too many recipients: received {received}, maximum is {max_allowed}",
            details={"max": max_allowed, "received": received},
        )


class NoValidRecipients(AppError):
    """Every recipient in the request was invalid; no job was created (422)."""

    code = "NO_VALID_RECIPIENTS"
    http_status = 422

    def __init__(self, invalid_rows: list[dict]) -> None:
        super().__init__("No valid recipients in the request", details=invalid_rows)


class InternalError(AppError):
    """Unexpected server-side failure (e.g. a file missing on disk).

    The message must never leak internals (tracebacks, paths, SQL) to the client (CLAUDE.md).
    """

    code = "INTERNAL_ERROR"
    http_status = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
