import pytest

from app.enums import CertificateStatus, JobStatus
from app.errors import CertificateNotFound, JobNotFound, NoValidRecipients, TooManyRecipients
from app.schemas import JobCreateRequest
from app.services import job_service
from tests.helpers import make_payload


def _request(**kwargs) -> JobCreateRequest:
    return JobCreateRequest.model_validate(make_payload(**kwargs))


def test_create_job_persists_valid_and_invalid_rows(session_factory, settings):
    invalid_item = {"name": "", "email": "bad@example.com"}
    request = _request(n_valid=2, invalid=[invalid_item])

    with session_factory() as session:
        created = job_service.create_job(session, request, settings)

    assert created.accepted == 2
    assert created.total == 3
    assert len(created.invalid) == 1

    with session_factory() as session:
        job = job_service.get_job(session, created.job_id)
        assert job.status == JobStatus.PENDING
        assert job.total_recipients == 3

        certs, total = job_service.list_certificates(session, created.job_id, None, 50, 0)
        assert total == 3
        valid_rows = [c for c in certs if c.status == CertificateStatus.PENDING]
        invalid_rows = [c for c in certs if c.status == CertificateStatus.INVALID]

        assert len(valid_rows) == 2
        for row in valid_rows:
            assert row.certificate_number is not None
            assert row.certificate_number.startswith("CERT-")

        assert len(invalid_rows) == 1
        bad = invalid_rows[0]
        assert bad.certificate_number is None
        assert bad.error_code is not None
        assert bad.error_message
        assert bad.error_details
        assert bad.raw_input == invalid_item


def test_create_job_zero_valid_raises_and_persists_nothing(session_factory, settings):
    request = _request(n_valid=0, invalid=[{"name": "", "email": "bad@example.com"}])

    with session_factory() as session:
        with pytest.raises(NoValidRecipients) as exc_info:
            job_service.create_job(session, request, settings)
        assert exc_info.value.details[0]["index"] == 0

    with session_factory() as session:
        from sqlalchemy import select

        from app.models import CertificateJob

        assert session.execute(select(CertificateJob)).first() is None


def test_create_job_too_many_recipients_raises_and_persists_nothing(session_factory, settings):
    request = _request(n_valid=settings.max_recipients_per_job + 1)

    with session_factory() as session:
        with pytest.raises(TooManyRecipients) as exc_info:
            job_service.create_job(session, request, settings)
        assert exc_info.value.details == {
            "max": settings.max_recipients_per_job,
            "received": settings.max_recipients_per_job + 1,
        }

    with session_factory() as session:
        from sqlalchemy import select

        from app.models import CertificateJob

        assert session.execute(select(CertificateJob)).first() is None


def test_get_job_not_found(session_factory):
    with session_factory() as session, pytest.raises(JobNotFound):
        job_service.get_job(session, "missing")


def test_get_certificate_not_found(session_factory):
    with session_factory() as session, pytest.raises(CertificateNotFound):
        job_service.get_certificate(session, "missing")


def test_list_certificates_unknown_job_raises(session_factory):
    with session_factory() as session, pytest.raises(JobNotFound):
        job_service.list_certificates(session, "missing", None, 50, 0)


def test_list_certificates_filter_order_pagination(session_factory, settings):
    request = _request(n_valid=5)
    with session_factory() as session:
        created = job_service.create_job(session, request, settings)

    with session_factory() as session:
        page1, total = job_service.list_certificates(session, created.job_id, None, 2, 0)
        page2, _ = job_service.list_certificates(session, created.job_id, None, 2, 2)

        assert total == 5
        assert [c.row_index for c in page1] == [0, 1]
        assert [c.row_index for c in page2] == [2, 3]

        succeeded_only, succeeded_total = job_service.list_certificates(
            session, created.job_id, CertificateStatus.SUCCEEDED, 50, 0
        )
        assert succeeded_total == 0
        assert succeeded_only == []


@pytest.mark.parametrize(
    "counts,expected",
    [
        ({"succeeded": 0, "failed": 0, "invalid": 0}, JobStatus.FAILED),
        ({"succeeded": 0, "failed": 5, "invalid": 0}, JobStatus.FAILED),
        ({"succeeded": 3, "failed": 0, "invalid": 0}, JobStatus.COMPLETED),
        ({"succeeded": 3, "failed": 1, "invalid": 0}, JobStatus.COMPLETED_WITH_ERRORS),
        ({"succeeded": 3, "failed": 0, "invalid": 1}, JobStatus.COMPLETED_WITH_ERRORS),
        ({"succeeded": 3, "failed": 1, "invalid": 1}, JobStatus.COMPLETED_WITH_ERRORS),
    ],
)
def test_compute_final_status_truth_table(counts, expected):
    assert job_service.compute_final_status(counts) == expected


def test_progress_maths_and_rounding(session_factory, settings):
    request = _request(n_valid=3, invalid=[{"name": "", "email": "bad@example.com"}])
    with session_factory() as session:
        created = job_service.create_job(session, request, settings)

    with session_factory() as session:
        progress = job_service.get_progress(session, created.job_id)
        assert progress.total == 4
        assert progress.invalid == 1
        assert progress.pending == 3
        assert progress.processable == 3
        assert progress.processed == 0
        assert progress.percent_complete == 0.0

        from app.models import Certificate

        certs = (
            session.query(Certificate)
            .filter(
                Certificate.job_id == created.job_id,
                Certificate.status == CertificateStatus.PENDING,
            )
            .all()
        )
        certs[0].status = CertificateStatus.SUCCEEDED
        session.commit()

        progress = job_service.get_progress(session, created.job_id)
        # 1 / 3 * 100 = 33.333... -> rounded to 1 dp
        assert progress.percent_complete == 33.3


def test_progress_processable_zero_is_100_percent(session_factory, settings):
    request = _request(n_valid=0, invalid=[{"name": "", "email": "bad@example.com"}])
    with pytest.raises(NoValidRecipients), session_factory() as session:
        job_service.create_job(session, request, settings)

    # Build a job with only invalid rows directly, since create_job refuses to persist one.
    from datetime import date

    from app.models import Certificate, CertificateJob

    with session_factory() as session:
        job = CertificateJob(
            program_name="P",
            issuer_name="I",
            issue_date=date(2026, 1, 1),
            total_recipients=1,
        )
        session.add(job)
        session.flush()
        session.add(
            Certificate(
                job_id=job.id,
                row_index=0,
                status=CertificateStatus.INVALID,
                error_code="VALIDATION_ERROR",
                error_message="bad",
            )
        )
        session.commit()
        job_id = job.id

    with session_factory() as session:
        progress = job_service.get_progress(session, job_id)
        assert progress.processable == 0
        assert progress.percent_complete == 100.0
