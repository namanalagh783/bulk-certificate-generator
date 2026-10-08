import threading

import sqlalchemy as sa

from app.enums import CertificateStatus, JobStatus
from app.models import Certificate, CertificateJob
from app.rendering.base import CertificateContent
from app.rendering.pdf_renderer import PdfCertificateRenderer
from app.schemas import JobCreateRequest
from app.services import job_service
from app.storage.base import certificate_key
from app.storage.local import LocalFileStorage
from app.workers.processor import JobProcessor
from tests.helpers import FailingRenderer, FailingStorage, make_payload


def _create_job(session_factory, settings, **kwargs):
    request = JobCreateRequest.model_validate(make_payload(**kwargs))
    with session_factory() as session:
        return job_service.create_job(session, request, settings)


def _job_status(session_factory, job_id):
    with session_factory() as session:
        return session.get(CertificateJob, job_id).status


def _cert_statuses(session_factory, job_id):
    with session_factory() as session:
        certs = session.query(Certificate).filter(Certificate.job_id == job_id).all()
        return {c.row_index: c.status for c in certs}


def test_all_succeed_completes_job_and_writes_files(session_factory, settings):
    created = _create_job(session_factory, settings, n_valid=3)
    storage = LocalFileStorage(settings.storage_dir)
    processor = JobProcessor(session_factory, PdfCertificateRenderer(), storage)

    processor.process(created.job_id)

    assert _job_status(session_factory, created.job_id) == JobStatus.COMPLETED
    with session_factory() as session:
        job = session.get(CertificateJob, created.job_id)
        assert job.started_at is not None
        assert job.completed_at is not None

        certs = session.query(Certificate).filter(Certificate.job_id == created.job_id).all()
        assert len(certs) == 3
        for cert in certs:
            assert cert.status == CertificateStatus.SUCCEEDED
            assert cert.file_key == certificate_key(created.job_id, cert.id)
            assert cert.file_size and cert.file_size > 0
            assert cert.generated_at is not None
            assert cert.attempts == 1
            assert storage.exists(cert.file_key)


def test_one_render_failure_others_succeed_completed_with_errors(session_factory, settings):
    created = _create_job(session_factory, settings, n_valid=3)
    storage = LocalFileStorage(settings.storage_dir)
    renderer = FailingRenderer(PdfCertificateRenderer(), fail_for_names={"Recipient 2"})
    processor = JobProcessor(session_factory, renderer, storage)

    processor.process(created.job_id)

    assert _job_status(session_factory, created.job_id) == JobStatus.COMPLETED_WITH_ERRORS
    with session_factory() as session:
        certs = session.query(Certificate).filter(Certificate.job_id == created.job_id).all()
        failed = [c for c in certs if c.recipient_name == "Recipient 2"]
        succeeded = [c for c in certs if c.recipient_name != "Recipient 2"]
        assert len(failed) == 1
        assert failed[0].status == CertificateStatus.FAILED
        assert failed[0].error_code == "RENDER_ERROR"
        assert failed[0].error_message
        assert len(succeeded) == 2
        assert all(c.status == CertificateStatus.SUCCEEDED for c in succeeded)


def test_storage_failure_marks_storage_error(session_factory, settings):
    created = _create_job(session_factory, settings, n_valid=2)
    with session_factory() as session:
        certs = session.query(Certificate).filter(Certificate.job_id == created.job_id).all()
        target_id = certs[0].id

    storage = FailingStorage(
        LocalFileStorage(settings.storage_dir), fail_for_keys_containing=target_id
    )
    processor = JobProcessor(session_factory, PdfCertificateRenderer(), storage)

    processor.process(created.job_id)

    with session_factory() as session:
        failed = session.get(Certificate, target_id)
        assert failed.status == CertificateStatus.FAILED
        assert failed.error_code == "STORAGE_ERROR"


def test_unexpected_exception_marks_internal_error(session_factory, settings):
    created = _create_job(session_factory, settings, n_valid=1)

    class BrokenRenderer:
        def render(self, content: CertificateContent) -> bytes:
            raise ValueError("boom")

    storage = LocalFileStorage(settings.storage_dir)
    processor = JobProcessor(session_factory, BrokenRenderer(), storage)

    processor.process(created.job_id)

    assert _job_status(session_factory, created.job_id) == JobStatus.FAILED
    with session_factory() as session:
        cert = session.query(Certificate).filter(Certificate.job_id == created.job_id).first()
        assert cert.status == CertificateStatus.FAILED
        assert cert.error_code == "INTERNAL_ERROR"
        assert "boom" in cert.error_message


def test_all_fail_job_is_failed(session_factory, settings):
    created = _create_job(session_factory, settings, n_valid=2)
    renderer = FailingRenderer(
        PdfCertificateRenderer(), fail_for_names={"Recipient 1", "Recipient 2"}
    )
    storage = LocalFileStorage(settings.storage_dir)
    processor = JobProcessor(session_factory, renderer, storage)

    processor.process(created.job_id)

    assert _job_status(session_factory, created.job_id) == JobStatus.FAILED


def test_valid_all_succeed_plus_invalid_rows_completed_with_errors(session_factory, settings):
    created = _create_job(
        session_factory, settings, n_valid=2, invalid=[{"name": "", "email": "bad@example.com"}]
    )
    storage = LocalFileStorage(settings.storage_dir)
    processor = JobProcessor(session_factory, PdfCertificateRenderer(), storage)

    processor.process(created.job_id)

    assert _job_status(session_factory, created.job_id) == JobStatus.COMPLETED_WITH_ERRORS


def test_processing_already_processing_job_is_noop(session_factory, settings):
    created = _create_job(session_factory, settings, n_valid=1)
    with session_factory() as session:
        job = session.get(CertificateJob, created.job_id)
        job.status = JobStatus.PROCESSING
        session.commit()

    storage = LocalFileStorage(settings.storage_dir)
    processor = JobProcessor(session_factory, PdfCertificateRenderer(), storage)
    processor.process(created.job_id)

    # Still processing: our claim UPDATE required status='pending', so nothing happened.
    assert _job_status(session_factory, created.job_id) == JobStatus.PROCESSING


def test_processing_finished_job_is_noop(session_factory, settings):
    created = _create_job(session_factory, settings, n_valid=1)
    storage = LocalFileStorage(settings.storage_dir)
    processor = JobProcessor(session_factory, PdfCertificateRenderer(), storage)
    processor.process(created.job_id)
    assert _job_status(session_factory, created.job_id) == JobStatus.COMPLETED

    # Processing again should be a no-op (job already terminal).
    processor.process(created.job_id)
    assert _job_status(session_factory, created.job_id) == JobStatus.COMPLETED


def test_progress_committed_incrementally(session_factory, settings):
    created = _create_job(session_factory, settings, n_valid=3)
    storage = LocalFileStorage(settings.storage_dir)
    real_renderer = PdfCertificateRenderer()
    seen_succeeded_before_last: list[int] = []
    render_count = {"n": 0}
    lock = threading.Lock()

    class ObserverRenderer:
        """Opens its own session mid-run to assert earlier certificates are already succeeded."""

        def render(self, content: CertificateContent) -> bytes:
            with lock:
                render_count["n"] += 1
                current = render_count["n"]
            if current > 1:
                with session_factory() as session:
                    counts = job_service.get_status_counts(session, created.job_id)
                    seen_succeeded_before_last.append(counts[CertificateStatus.SUCCEEDED])
            return real_renderer.render(content)

    processor = JobProcessor(session_factory, ObserverRenderer(), storage)
    processor.process(created.job_id)

    assert _job_status(session_factory, created.job_id) == JobStatus.COMPLETED
    # By the time the 2nd/3rd certificate renders, at least the previous one(s) already succeeded.
    assert seen_succeeded_before_last
    assert all(count >= 1 for count in seen_succeeded_before_last)


class _RaisingInLoopProcessor(JobProcessor):
    """Forces the exception-escapes-the-loop path (ARCHITECTURE §7.2 step 5)."""

    def _pending_certificate_ids(self, job_id):
        raise RuntimeError("boom in loop")


class _CountLimitedSessionFactory:
    """Delegates to a real session factory, then raises once a call budget is exhausted.

    Used to simulate the `last_error` write itself failing, without touching app/ code.
    """

    def __init__(self, inner, calls_allowed: int) -> None:
        self._inner = inner
        self._calls_allowed = calls_allowed
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls > self._calls_allowed:
            raise RuntimeError("session factory unavailable")
        return self._inner()


def test_unexpected_exception_in_loop_leaves_job_processing_with_last_error(
    session_factory, settings
):
    created = _create_job(session_factory, settings, n_valid=1)
    storage = LocalFileStorage(settings.storage_dir)
    processor = _RaisingInLoopProcessor(session_factory, PdfCertificateRenderer(), storage)

    processor.process(created.job_id)  # must not raise

    with session_factory() as session:
        job = session.get(CertificateJob, created.job_id)
        # Claimed (pending -> processing) but never finalised: the exception pre-empted _finalise.
        assert job.status == JobStatus.PROCESSING
        assert job.last_error == "Unexpected error during processing"


def test_last_error_write_failure_is_swallowed(session_factory, settings):
    created = _create_job(session_factory, settings, n_valid=1)
    storage = LocalFileStorage(settings.storage_dir)
    # 1 call budget: _claim_job succeeds (call #1); _record_job_error's own session (call #2) fails.
    flaky_factory = _CountLimitedSessionFactory(session_factory, calls_allowed=1)
    processor = _RaisingInLoopProcessor(flaky_factory, PdfCertificateRenderer(), storage)

    processor.process(created.job_id)  # must not raise even though the error write itself fails

    with session_factory() as session:
        job = session.get(CertificateJob, created.job_id)
        assert job.status == JobStatus.PROCESSING
        assert job.last_error is None  # the write never landed


class _RaceConditionProcessor(JobProcessor):
    """Flips one certificate to PROCESSING right after listing, simulating another worker
    claiming it between `_pending_certificate_ids` and `_claim_certificate` (ARCHITECTURE §7.2).
    """

    def __init__(self, *args, flip_certificate_id: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._flip_certificate_id = flip_certificate_id
        self._flipped = False

    def _pending_certificate_ids(self, job_id):
        ids = super()._pending_certificate_ids(job_id)
        if not self._flipped:
            self._flipped = True
            with self._session_factory() as session:
                session.execute(
                    sa.update(Certificate)
                    .where(Certificate.id == self._flip_certificate_id)
                    .values(status=CertificateStatus.PROCESSING)
                )
                session.commit()
        return ids


def test_certificate_claim_race_is_skipped_others_still_processed(session_factory, settings):
    created = _create_job(session_factory, settings, n_valid=2)
    with session_factory() as session:
        certs = sorted(
            session.query(Certificate).filter(Certificate.job_id == created.job_id).all(),
            key=lambda c: c.row_index,
        )
        raced_id = certs[0].id

    storage = LocalFileStorage(settings.storage_dir)
    processor = _RaceConditionProcessor(
        session_factory, PdfCertificateRenderer(), storage, flip_certificate_id=raced_id
    )

    processor.process(created.job_id)

    with session_factory() as session:
        raced = session.get(Certificate, raced_id)
        other = (
            session.query(Certificate)
            .filter(Certificate.job_id == created.job_id, Certificate.id != raced_id)
            .one()
        )
        # Claim affected 0 rows: skipped, left exactly as the race left it, never rendered.
        assert raced.status == CertificateStatus.PROCESSING
        assert raced.attempts == 0
        assert raced.file_key is None
        # The other certificate is unaffected and still gets processed normally.
        assert other.status == CertificateStatus.SUCCEEDED

    # Final status counts only succeeded/failed/invalid, so the job still reaches a terminal state.
    assert _job_status(session_factory, created.job_id) == JobStatus.COMPLETED
