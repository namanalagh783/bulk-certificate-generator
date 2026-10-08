import threading
import time

from app.enums import CertificateStatus, JobStatus
from app.models import Certificate, CertificateJob
from app.rendering.pdf_renderer import PdfCertificateRenderer
from app.schemas import JobCreateRequest
from app.services import job_service
from app.storage.local import LocalFileStorage
from app.workers.dispatcher import InlineDispatcher, ThreadPoolDispatcher
from app.workers.processor import JobProcessor
from tests.helpers import ManualDispatcher, make_payload


def _create_job(session_factory, settings, **kwargs):
    request = JobCreateRequest.model_validate(make_payload(**kwargs))
    with session_factory() as session:
        return job_service.create_job(session, request, settings)


def _wait_for_status(session_factory, job_id, status, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with session_factory() as session:
            job = session.get(CertificateJob, job_id)
            if job.status == status:
                return True
        time.sleep(0.05)
    return False


def test_threadpool_dispatcher_processes_job_in_background(session_factory, settings):
    created = _create_job(session_factory, settings, n_valid=2)
    storage = LocalFileStorage(settings.storage_dir)
    processor = JobProcessor(session_factory, PdfCertificateRenderer(), storage)
    dispatcher = ThreadPoolDispatcher(processor.process, max_workers=2)

    try:
        dispatcher.submit(created.job_id)
        assert _wait_for_status(session_factory, created.job_id, JobStatus.COMPLETED, timeout=5.0)
    finally:
        dispatcher.shutdown()


def test_threadpool_dispatcher_pool_still_runs_after_failure(session_factory, settings):
    calls: list[str] = []
    lock = threading.Lock()

    def process(job_id: str) -> None:
        if job_id == "fail":
            raise RuntimeError("boom")
        with lock:
            calls.append(job_id)

    dispatcher = ThreadPoolDispatcher(process, max_workers=1)
    try:
        dispatcher.submit("fail")
        dispatcher.submit("ok")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and "ok" not in calls:
            time.sleep(0.02)
        assert calls == ["ok"]
    finally:
        dispatcher.shutdown()


def test_inline_dispatcher_runs_immediately(session_factory, settings):
    created = _create_job(session_factory, settings, n_valid=1)
    storage = LocalFileStorage(settings.storage_dir)
    processor = JobProcessor(session_factory, PdfCertificateRenderer(), storage)
    dispatcher = InlineDispatcher(processor.process)

    dispatcher.submit(created.job_id)

    with session_factory() as session:
        job = session.get(CertificateJob, created.job_id)
        assert job.status == JobStatus.COMPLETED
    dispatcher.shutdown()


def test_lifespan_recovery_resubmits_stuck_job(settings, session_factory, make_client):
    created = _create_job(session_factory, settings, n_valid=1)
    with session_factory() as session:
        job = session.get(CertificateJob, created.job_id)
        job.status = JobStatus.PROCESSING
        certs = session.query(Certificate).filter(Certificate.job_id == created.job_id).all()
        certs[0].status = CertificateStatus.PROCESSING
        session.commit()

    settings.recover_on_startup = True
    manual = ManualDispatcher()
    make_client(settings, dispatcher=manual)

    assert manual.submitted == [created.job_id]
