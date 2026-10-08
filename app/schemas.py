"""Pydantic request/response models for the API (added in Phase 2).

Field names and shapes mirror docs/API_SPEC.md exactly. Input models forbid unknown fields and
strip surrounding whitespace from strings (CLAUDE.md, ARCHITECTURE §2); response models stay
permissive since they're built by our own services, not by client input.
"""

import unicodedata
from datetime import UTC, date, datetime
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    PlainSerializer,
    field_validator,
)


def _format_utc(value: datetime) -> str:
    """Render a datetime as UTC ISO-8601 with second precision and a trailing 'Z'.

    Naive values are treated as already being UTC (CLAUDE.md: SQLite returns naive datetimes,
    so the response layer is where that gets normalised); aware values are converted.
    """
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


# Reusable annotated type: attach the serializer once, use it on every timestamp field. A `None`
# value on an `UtcDateTime | None` field bypasses the serializer entirely (it only applies to the
# `datetime` branch of the union), so nullable timestamps stay `null`.
UtcDateTime = Annotated[datetime, PlainSerializer(_format_utc, return_type=str)]


# --------------------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------------------


class CertificateInfoIn(BaseModel):
    """Shared certificate details supplied once per job (API_SPEC.md POST .../certificate-jobs)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(default="Certificate of Completion", min_length=1, max_length=100)
    program_name: str = Field(min_length=1, max_length=200)
    issuer_name: str = Field(min_length=1, max_length=200)
    issue_date: date
    signatory_name: str | None = Field(default=None, max_length=120)
    signatory_title: str | None = Field(default=None, max_length=120)

    @field_validator("signatory_name", "signatory_title", mode="after")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        """An empty string after trimming means "not provided" (API_SPEC.md)."""
        if value == "":
            return None
        return value


class RecipientIn(BaseModel):
    """One recipient row (API_SPEC.md "Per-recipient rules").

    Validated per-item via `RecipientIn.model_validate` from `validate_recipients()`, not as part
    of the envelope schema, so one bad row doesn't reject the whole request (ADR-004).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str
    email: EmailStr

    @field_validator("name", mode="before")
    @classmethod
    def _normalise_name(cls, value: Any) -> str:
        """Normalise, collapse whitespace, and enforce the name rules in order (API_SPEC.md).

        Runs in "before" mode (on the raw value, prior to any type coercion) so non-str input is
        rejected outright rather than silently coerced to a string.
        """
        if not isinstance(value, str):
            raise ValueError("name must be a string")
        normalised = unicodedata.normalize("NFC", value)
        collapsed = " ".join(normalised.split())
        if any(unicodedata.category(ch) in ("Cc", "Cf") for ch in collapsed):
            raise ValueError("name must not contain control characters")
        if not (1 <= len(collapsed) <= 100):
            raise ValueError("name must be between 1 and 100 characters")
        if not any(ch.isalpha() for ch in collapsed):
            raise ValueError("name must contain at least one letter")
        return collapsed


class JobCreateRequest(BaseModel):
    """The top-level request body for POST /api/v1/certificate-jobs."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    certificate: CertificateInfoIn
    # `list[Any]`, deliberately untyped per-item and with NO max_length (ADR-004, ADR-018): the
    # service validates each item individually and enforces MAX_RECIPIENTS_PER_JOB itself so that
    # going over the limit returns TOO_MANY_RECIPIENTS rather than a generic VALIDATION_ERROR.
    recipients: list[Any] = Field(min_length=1)


# --------------------------------------------------------------------------------------
# Shared response fragments
# --------------------------------------------------------------------------------------


class LinksOut(BaseModel):
    """Convenience hyperlinks included on job responses (API_SPEC.md)."""

    model_config = ConfigDict(populate_by_name=True)

    # "self" is a Python keyword-ish builtin name, so the attribute is `self_` with an alias that
    # makes the JSON key exactly "self".
    self_: str = Field(alias="self", serialization_alias="self")
    certificates: str
    download: str


class FieldErrorOut(BaseModel):
    """One field-level validation error, as emitted inside an invalid-row object."""

    field: str | None
    message: str


class InvalidRecipientOut(BaseModel):
    """An invalid-row object (API_SPEC.md), used in job-created and NO_VALID_RECIPIENTS."""

    index: int
    code: str
    errors: list[FieldErrorOut]


class SummaryOut(BaseModel):
    """Counts returned in the job-created response."""

    total: int
    accepted: int
    invalid: int


class CertificateInfoOut(BaseModel):
    """Certificate details as echoed back on job responses."""

    model_config = ConfigDict(from_attributes=True)

    title: str
    program_name: str
    issuer_name: str
    issue_date: date
    signatory_name: str | None
    signatory_title: str | None


class ProgressOut(BaseModel):
    """Derived progress counts (ARCHITECTURE §6)."""

    total: int
    invalid: int
    pending: int
    processing: int
    succeeded: int
    failed: int
    processable: int
    processed: int
    percent_complete: float


class RecipientOut(BaseModel):
    """Recipient identity as shown on a certificate item."""

    model_config = ConfigDict(from_attributes=True)

    name: str | None
    email: str | None


class CertificateErrorOut(BaseModel):
    """Error details attached to a failed or invalid certificate item."""

    code: str
    message: str
    details: Any | None = None


# --------------------------------------------------------------------------------------
# Top-level responses
# --------------------------------------------------------------------------------------


class JobCreatedOut(BaseModel):
    """202 response body for POST /api/v1/certificate-jobs."""

    job_id: str
    status: str
    summary: SummaryOut
    invalid_recipients: list[InvalidRecipientOut]
    links: LinksOut


class JobOut(BaseModel):
    """200 response body for GET /api/v1/certificate-jobs/{job_id}."""

    model_config = ConfigDict(from_attributes=True)

    job_id: str
    status: str
    template_id: str
    certificate: CertificateInfoOut
    progress: ProgressOut
    created_at: UtcDateTime
    started_at: UtcDateTime | None
    completed_at: UtcDateTime | None
    links: LinksOut


class CertificateOut(BaseModel):
    """One item in a certificate list, or the body of GET .../certificates/{id}."""

    model_config = ConfigDict(from_attributes=True)

    certificate_id: str
    job_id: str
    index: int
    status: str
    recipient: RecipientOut
    certificate_number: str | None
    generated_at: UtcDateTime | None
    error: CertificateErrorOut | None
    download_url: str | None


class PaginationOut(BaseModel):
    """Pagination echo for list responses."""

    limit: int
    offset: int
    total: int


class CertificateListOut(BaseModel):
    """200 response body for GET /api/v1/certificate-jobs/{job_id}/certificates."""

    items: list[CertificateOut]
    pagination: PaginationOut


class ErrorBody(BaseModel):
    """The inner `error` object of the uniform error envelope (API_SPEC.md)."""

    code: str
    message: str
    details: Any | None = None


class ErrorEnvelope(BaseModel):
    """The uniform error envelope returned by every error response."""

    error: ErrorBody
