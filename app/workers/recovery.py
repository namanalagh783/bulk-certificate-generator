"""Startup recovery: re-queue jobs interrupted by a crash or restart (ARCHITECTURE §7.5).

Safe because file keys are deterministic and writes are atomic (ADR-007): re-rendering a
recovered certificate overwrites its file cleanly. This module only resets state in the DB; it
does not submit to a dispatcher (the lifespan does that, since this module never imports one).
"""

import logging

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.enums import CertificateStatus, JobStatus
from app.models import Certificate, CertificateJob

logger = logging.getLogger(__name__)

_INCOMPLETE_JOB_STATUSES = (JobStatus.PENDING, JobStatus.PROCESSING)


def recover_incomplete_jobs(session_factory: sessionmaker[Session]) -> list[str]:
    """Reset incomplete jobs/certificates to `pending`; return the affected job ids.

    Ids are ordered by `created_at` so recovery is deterministic and FIFO-ish.
    """
    with session_factory() as session:
        job_ids = list(
            session.execute(
                sa.select(CertificateJob.id)
                .where(CertificateJob.status.in_(_INCOMPLETE_JOB_STATUSES))
                .order_by(CertificateJob.created_at)
            )
            .scalars()
            .all()
        )

        if not job_ids:
            return []

        session.execute(
            sa.update(Certificate)
            .where(
                Certificate.job_id.in_(job_ids),
                Certificate.status == CertificateStatus.PROCESSING,
            )
            .values(status=CertificateStatus.PENDING)
        )
        session.execute(
            sa.update(CertificateJob)
            .where(CertificateJob.id.in_(job_ids))
            .values(status=JobStatus.PENDING)
        )
        session.commit()

    logger.info("Recovered %d incomplete job(s): %s", len(job_ids), job_ids)
    return job_ids
