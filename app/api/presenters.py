"""Pure ORM/service-result -> response-model mapping (ADR-019).

Both routers need the certificate mapper, so it lives here rather than in either route module.
No FastAPI imports: these are plain functions over our own models and dataclasses.
"""

from app.enums import CertificateStatus
from app.models import Certificate, CertificateJob
from app.schemas import (
    CertificateErrorOut,
    CertificateInfoOut,
    CertificateOut,
    FieldErrorOut,
    InvalidRecipientOut,
    JobCreatedOut,
    JobOut,
    LinksOut,
    ProgressOut,
    RecipientOut,
    SummaryOut,
)
from app.services.job_service import JobCreated, Progress


def links_for_job(job_id: str) -> LinksOut:
    """The three hyperlinks included on every job response (API_SPEC.md)."""
    base = f"/api/v1/certificate-jobs/{job_id}"
    return LinksOut(self=base, certificates=f"{base}/certificates", download=f"{base}/download")


def job_created_out(created: JobCreated) -> JobCreatedOut:
    """202 response body: the acceptance snapshot (ARCHITECTURE §7.1 step 7).

    `status` is always "pending" here regardless of what the dispatcher has already done with
    the job, since this reflects the state at the moment the transaction committed.
    """
    return JobCreatedOut(
        job_id=created.job_id,
        status="pending",
        summary=SummaryOut(
            total=created.total, accepted=created.accepted, invalid=len(created.invalid)
        ),
        invalid_recipients=[
            InvalidRecipientOut(
                index=inv.index,
                code=str(inv.code),
                errors=[FieldErrorOut(field=e.field, message=e.message) for e in inv.errors],
            )
            for inv in created.invalid
        ],
        links=links_for_job(created.job_id),
    )


def job_out(job: CertificateJob, progress: Progress) -> JobOut:
    """200 response body for GET /api/v1/certificate-jobs/{job_id}."""
    return JobOut(
        job_id=job.id,
        status=job.status,
        template_id=job.template_id,
        certificate=CertificateInfoOut.model_validate(job),
        progress=ProgressOut(
            total=progress.total,
            invalid=progress.invalid,
            pending=progress.pending,
            processing=progress.processing,
            succeeded=progress.succeeded,
            failed=progress.failed,
            processable=progress.processable,
            processed=progress.processed,
            percent_complete=progress.percent_complete,
        ),
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        links=links_for_job(job.id),
    )


def certificate_out(cert: Certificate) -> CertificateOut:
    """One certificate list item / the single-certificate response (API_SPEC.md).

    `download_url` is only populated for `succeeded` rows (there is nothing to download
    otherwise); `error` is populated whenever an error code is set, whatever the status.
    """
    error = None
    if cert.error_code is not None:
        error = CertificateErrorOut(
            code=cert.error_code,
            message=cert.error_message or "",
            details=cert.error_details,
        )

    download_url = None
    if cert.status == CertificateStatus.SUCCEEDED:
        download_url = f"/api/v1/certificates/{cert.id}/download"

    return CertificateOut(
        certificate_id=cert.id,
        job_id=cert.job_id,
        index=cert.row_index,
        status=cert.status,
        recipient=RecipientOut(name=cert.recipient_name, email=cert.recipient_email),
        certificate_number=cert.certificate_number,
        generated_at=cert.generated_at,
        error=error,
        download_url=download_url,
    )
