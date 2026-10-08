"""Job creation and read-side queries (ARCHITECTURE §7.1, §7.3; ADR-005).

No FastAPI imports: this module raises `AppError` subclasses and the API layer translates them.
Everything here takes a `Session` the caller owns; this module never opens its own.
"""

from collections.abc import Mapping
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import CertificateStatus, JobStatus
from app.errors import CertificateNotFound, JobNotFound, NoValidRecipients, TooManyRecipients
from app.models import Certificate, CertificateJob
from app.schemas import JobCreateRequest
from app.services.validation import InvalidRecipient, validate_recipients
from app.utils import new_certificate_number


@dataclass(frozen=True)
class JobCreated:
    """Result of `create_job`: enough to build the 202 response (API_SPEC.md)."""

    job_id: str
    total: int
    accepted: int
    invalid: list[InvalidRecipient]


def create_job(session: Session, request: JobCreateRequest, settings: Settings) -> JobCreated:
    """Validate and persist a job in one transaction (ARCHITECTURE §7.1 steps 2-5).

    Does NOT dispatch: the caller (the API route, in Phase 5) submits the job id to the
    dispatcher only after this function's transaction has committed, so a worker thread never
    races the writer to see the rows (ARCHITECTURE §7.1 step 6).
    """
    if len(request.recipients) > settings.max_recipients_per_job:
        raise TooManyRecipients(settings.max_recipients_per_job, len(request.recipients))

    result = validate_recipients(request.recipients)
    if not result.valid:
        raise NoValidRecipients([inv.to_dict() for inv in result.invalid])

    cert_info = request.certificate
    job = CertificateJob(
        status=JobStatus.PENDING,
        title=cert_info.title,
        program_name=cert_info.program_name,
        issuer_name=cert_info.issuer_name,
        issue_date=cert_info.issue_date,
        signatory_name=cert_info.signatory_name,
        signatory_title=cert_info.signatory_title,
        total_recipients=len(request.recipients),
    )
    session.add(job)
    session.flush()  # assign job.id before building certificate rows below

    for item in result.valid:
        session.add(
            Certificate(
                job_id=job.id,
                row_index=item.index,
                status=CertificateStatus.PENDING,
                recipient_name=item.name,
                recipient_email=item.email,
                certificate_number=new_certificate_number(),
            )
        )

    for inv in result.invalid:
        session.add(
            Certificate(
                job_id=job.id,
                row_index=inv.index,
                status=CertificateStatus.INVALID,
                recipient_name=inv.name,
                recipient_email=inv.email,
                raw_input=inv.raw,
                error_code=str(inv.code),
                error_message=inv.message,
                error_details=[{"field": e.field, "message": e.message} for e in inv.errors],
            )
        )

    session.commit()

    return JobCreated(
        job_id=job.id,
        total=len(request.recipients),
        accepted=len(result.valid),
        invalid=result.invalid,
    )


def get_job(session: Session, job_id: str) -> CertificateJob:
    """Fetch a job by id or raise `JobNotFound`."""
    job = session.get(CertificateJob, job_id)
    if job is None:
        raise JobNotFound(job_id)
    return job


def get_status_counts(session: Session, job_id: str) -> dict[str, int]:
    """One `GROUP BY status` query; every `CertificateStatus` key is present (0 if absent)."""
    rows = session.execute(
        sa.select(Certificate.status, sa.func.count())
        .where(Certificate.job_id == job_id)
        .group_by(Certificate.status)
    ).all()
    counts = {status: 0 for status in CertificateStatus}
    for status, count in rows:
        counts[status] = count
    return counts


@dataclass(frozen=True)
class Progress:
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


def get_progress(session: Session, job_id: str) -> Progress:
    """Compute progress from the status counts using the §6 maths."""
    counts = get_status_counts(session, job_id)
    invalid = counts[CertificateStatus.INVALID]
    pending = counts[CertificateStatus.PENDING]
    processing = counts[CertificateStatus.PROCESSING]
    succeeded = counts[CertificateStatus.SUCCEEDED]
    failed = counts[CertificateStatus.FAILED]
    total = invalid + pending + processing + succeeded + failed

    processable = total - invalid
    processed = succeeded + failed
    percent_complete = 100.0 if processable == 0 else round(processed / processable * 100, 1)

    return Progress(
        total=total,
        invalid=invalid,
        pending=pending,
        processing=processing,
        succeeded=succeeded,
        failed=failed,
        processable=processable,
        processed=processed,
        percent_complete=percent_complete,
    )


def list_certificates(
    session: Session,
    job_id: str,
    status: CertificateStatus | None,
    limit: int,
    offset: int,
) -> tuple[list[Certificate], int]:
    """Filtered, paginated, `row_index`-ordered certificates for a job, plus the filtered total."""
    get_job(session, job_id)  # raises JobNotFound if the job doesn't exist

    base = sa.select(Certificate).where(Certificate.job_id == job_id)
    count_query = (
        sa.select(sa.func.count()).select_from(Certificate).where(Certificate.job_id == job_id)
    )
    if status is not None:
        base = base.where(Certificate.status == status)
        count_query = count_query.where(Certificate.status == status)

    total = session.execute(count_query).scalar_one()
    items = (
        session.execute(base.order_by(Certificate.row_index).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return list(items), total


def get_certificate(session: Session, certificate_id: str) -> Certificate:
    """Fetch a certificate by id or raise `CertificateNotFound`."""
    certificate = session.get(Certificate, certificate_id)
    if certificate is None:
        raise CertificateNotFound(certificate_id)
    return certificate


def compute_final_status(counts: Mapping[str, int]) -> JobStatus:
    """Final job status from terminal certificate counts (ADR-005 truth table exactly)."""
    succeeded = counts[CertificateStatus.SUCCEEDED]
    failed = counts[CertificateStatus.FAILED]
    invalid = counts[CertificateStatus.INVALID]

    if succeeded == 0:
        return JobStatus.FAILED
    if failed == 0 and invalid == 0:
        return JobStatus.COMPLETED
    return JobStatus.COMPLETED_WITH_ERRORS
