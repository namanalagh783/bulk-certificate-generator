"""JobProcessor: claim -> render -> store -> mark, per certificate, with failure isolation.

Implements ARCHITECTURE §7.2 exactly. Every unit of work uses its own short-lived `Session`;
only certificate/job IDs cross thread boundaries (CLAUDE.md). Rendering and storage happen with
no DB session open, so a slow render never holds a DB connection.
"""

import logging
from collections.abc import Callable

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.enums import CertificateStatus, JobStatus
from app.models import Certificate, CertificateJob
from app.rendering.base import CertificateContent, CertificateRenderer, RenderError
from app.services import job_service
from app.storage.base import CertificateStorage, StorageError, certificate_key
from app.utils import utcnow

logger = logging.getLogger(__name__)

# Max length stored for `error_message`/`last_error` (CLAUDE.md: no tracebacks, bounded size).
_ERROR_MESSAGE_LIMIT = 500


def _build_content(job: CertificateJob, certificate: Certificate) -> CertificateContent:
    """Build the immutable render input from job + certificate fields."""
    assert certificate.recipient_name is not None  # pending certs always have a name
    return CertificateContent(
        certificate_number=certificate.certificate_number or "",
        recipient_name=certificate.recipient_name,
        title=job.title,
        program_name=job.program_name,
        issuer_name=job.issuer_name,
        issue_date=job.issue_date,
        signatory_name=job.signatory_name,
        signatory_title=job.signatory_title,
    )


class JobProcessor:
    """Processes one job end-to-end: claim, render/store each certificate, finalise."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        renderer: CertificateRenderer,
        storage: CertificateStorage,
    ) -> None:
        self._session_factory = session_factory
        self._renderer = renderer
        self._storage = storage

    def process(self, job_id: str) -> None:
        """Entry point handed to the dispatcher as `Callable[[str], None]`."""
        if not self._claim_job(job_id):
            logger.info("Job %s not claimed (not pending); skipping", job_id)
            return

        logger.info("Job %s claimed for processing", job_id)
        try:
            certificate_ids = self._pending_certificate_ids(job_id)
            for certificate_id in certificate_ids:
                self._process_one(job_id, certificate_id)
            self._finalise(job_id)
        except Exception:  # noqa: BLE001 - must never crash the worker thread
            logger.exception("Unexpected error while processing job %s", job_id)
            self._record_job_error(job_id, "Unexpected error during processing")

    def _claim_job(self, job_id: str) -> bool:
        """Conditional UPDATE: pending -> processing. Returns whether this call won the claim."""
        with self._session_factory() as session:
            now = utcnow()
            result = session.execute(
                sa.update(CertificateJob)
                .where(CertificateJob.id == job_id, CertificateJob.status == JobStatus.PENDING)
                .values(
                    status=JobStatus.PROCESSING,
                    started_at=sa.func.coalesce(CertificateJob.started_at, now),
                    updated_at=now,
                )
            )
            session.commit()
            return result.rowcount > 0

    def _pending_certificate_ids(self, job_id: str) -> list[str]:
        with self._session_factory() as session:
            rows = (
                session.execute(
                    sa.select(Certificate.id)
                    .where(
                        Certificate.job_id == job_id,
                        Certificate.status == CertificateStatus.PENDING,
                    )
                    .order_by(Certificate.row_index)
                )
                .scalars()
                .all()
            )
            return list(rows)

    def _claim_certificate(self, certificate_id: str) -> CertificateContent | None:
        """Conditional claim + build render content, in one short session."""
        with self._session_factory() as session:
            result = session.execute(
                sa.update(Certificate)
                .where(
                    Certificate.id == certificate_id,
                    Certificate.status == CertificateStatus.PENDING,
                )
                .values(status=CertificateStatus.PROCESSING, attempts=Certificate.attempts + 1)
            )
            if result.rowcount == 0:
                session.commit()
                return None

            certificate = session.get(Certificate, certificate_id)
            job = session.get(CertificateJob, certificate.job_id)
            content = _build_content(job, certificate)
            session.commit()
            return content

    def _process_one(self, job_id: str, certificate_id: str) -> None:
        content = self._claim_certificate(certificate_id)
        if content is None:
            logger.info(
                "Certificate %s (job %s) not claimed (not pending); skipping",
                certificate_id,
                job_id,
            )
            return

        try:
            data = self._renderer.render(content)
            key = certificate_key(job_id, certificate_id)
            size = self._storage.save(key, data)
        except RenderError as exc:
            self._mark_failed(job_id, certificate_id, "RENDER_ERROR", exc)
            return
        except StorageError as exc:
            self._mark_failed(job_id, certificate_id, "STORAGE_ERROR", exc)
            return
        except Exception as exc:  # noqa: BLE001 - classify as INTERNAL_ERROR, keep looping
            self._mark_failed(job_id, certificate_id, "INTERNAL_ERROR", exc)
            return

        self._mark_succeeded(job_id, certificate_id, key, size)

    def _mark_succeeded(self, job_id: str, certificate_id: str, key: str, size: int) -> None:
        with self._session_factory() as session:
            session.execute(
                sa.update(Certificate)
                .where(Certificate.id == certificate_id)
                .values(
                    status=CertificateStatus.SUCCEEDED,
                    file_key=key,
                    file_size=size,
                    generated_at=utcnow(),
                )
            )
            session.commit()
        logger.info("Certificate %s (job %s) succeeded", certificate_id, job_id)

    def _mark_failed(
        self, job_id: str, certificate_id: str, error_code: str, exc: Exception
    ) -> None:
        message = str(exc)[:_ERROR_MESSAGE_LIMIT]
        with self._session_factory() as session:
            session.execute(
                sa.update(Certificate)
                .where(Certificate.id == certificate_id)
                .values(
                    status=CertificateStatus.FAILED,
                    error_code=error_code,
                    error_message=message,
                )
            )
            session.commit()
        logger.exception("Certificate %s (job %s) failed: %s", certificate_id, job_id, error_code)

    def _finalise(self, job_id: str) -> None:
        with self._session_factory() as session:
            counts = job_service.get_status_counts(session, job_id)
            final_status = job_service.compute_final_status(counts)
            session.execute(
                sa.update(CertificateJob)
                .where(CertificateJob.id == job_id, CertificateJob.status == JobStatus.PROCESSING)
                .values(status=final_status, completed_at=utcnow())
            )
            session.commit()
        logger.info("Job %s finished with status %s", job_id, final_status)

    def _record_job_error(self, job_id: str, message: str) -> None:
        """Best-effort write of `last_error`; swallow failures (ARCHITECTURE §7.2 step 5)."""
        try:
            with self._session_factory() as session:
                session.execute(
                    sa.update(CertificateJob)
                    .where(CertificateJob.id == job_id)
                    .values(last_error=message[:_ERROR_MESSAGE_LIMIT])
                )
                session.commit()
        except Exception:  # noqa: BLE001 - this write is best-effort by design
            logger.exception("Failed to record last_error for job %s", job_id)


ProcessJobCallable = Callable[[str], None]
