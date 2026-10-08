"""JobStatus, CertificateStatus and ErrorCode string enums.

StrEnum (not a plain Enum) so values serialise naturally to JSON and compare equal to plain
strings in model columns, which are stored as String(32) for portability across SQLite/Postgres
(see ARCHITECTURE §4, §5).
"""

import enum


class JobStatus(enum.StrEnum):
    """Lifecycle of a certificate generation job (ARCHITECTURE §6)."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


class CertificateStatus(enum.StrEnum):
    """Lifecycle of a single certificate row (ARCHITECTURE §6)."""

    INVALID = "invalid"
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ErrorCode(enum.StrEnum):
    """Per-certificate error codes (ARCHITECTURE §5)."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    DUPLICATE_RECIPIENT = "DUPLICATE_RECIPIENT"
    RENDER_ERROR = "RENDER_ERROR"
    STORAGE_ERROR = "STORAGE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# Terminal job statuses: once reached, a job will not transition further except via recovery
# resetting it back to pending (ARCHITECTURE §6). Later phases (processor, recovery, API) need
# this set to decide whether a job is "done".
TERMINAL_JOB_STATUSES = frozenset(
    {JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_ERRORS, JobStatus.FAILED}
)
