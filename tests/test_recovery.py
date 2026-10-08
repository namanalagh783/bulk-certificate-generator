from app.enums import CertificateStatus, JobStatus
from app.models import Certificate, CertificateJob
from app.rendering.pdf_renderer import PdfCertificateRenderer
from app.schemas import JobCreateRequest
from app.services import job_service
from app.storage.local import LocalFileStorage
from app.workers.processor import JobProcessor
from app.workers.recovery import recover_incomplete_jobs
from tests.helpers import make_payload


def _create_job(session_factory, settings, **kwargs):
    request = JobCreateRequest.model_validate(make_payload(**kwargs))
    with session_factory() as session:
        return job_service.create_job(session, request, settings)


def test_recover_resets_stuck_job_and_certificate(session_factory, settings):
    created = _create_job(session_factory, settings, n_valid=2)
    with session_factory() as session:
        job = session.get(CertificateJob, created.job_id)
        job.status = JobStatus.PROCESSING
        certs = session.query(Certificate).filter(Certificate.job_id == created.job_id).all()
        certs[0].status = CertificateStatus.PROCESSING
        session.commit()

    recovered_ids = recover_incomplete_jobs(session_factory)

    assert recovered_ids == [created.job_id]
    with session_factory() as session:
        job = session.get(CertificateJob, created.job_id)
        assert job.status == JobStatus.PENDING
        certs = session.query(Certificate).filter(Certificate.job_id == created.job_id).all()
        assert all(c.status == CertificateStatus.PENDING for c in certs)

    storage = LocalFileStorage(settings.storage_dir)
    processor = JobProcessor(session_factory, PdfCertificateRenderer(), storage)
    processor.process(created.job_id)

    with session_factory() as session:
        job = session.get(CertificateJob, created.job_id)
        assert job.status == JobStatus.COMPLETED


def test_recover_leaves_terminal_jobs_untouched(session_factory, settings):
    created = _create_job(session_factory, settings, n_valid=1)
    with session_factory() as session:
        job = session.get(CertificateJob, created.job_id)
        job.status = JobStatus.COMPLETED
        session.commit()

    recovered_ids = recover_incomplete_jobs(session_factory)

    assert recovered_ids == []
    with session_factory() as session:
        job = session.get(CertificateJob, created.job_id)
        assert job.status == JobStatus.COMPLETED
