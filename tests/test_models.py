from datetime import date, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.enums import CertificateStatus, JobStatus
from app.models import Certificate, CertificateJob


def _make_job(**overrides) -> CertificateJob:
    defaults = dict(
        program_name="Intro to Testing",
        issuer_name="Acme University",
        issue_date=date(2026, 1, 1),
        total_recipients=1,
    )
    defaults.update(overrides)
    return CertificateJob(**defaults)


def test_insert_and_read_job_with_certificates(session_factory):
    session = session_factory()
    try:
        job = _make_job(total_recipients=2)
        session.add(job)
        session.flush()

        cert1 = Certificate(
            job_id=job.id,
            row_index=0,
            status=CertificateStatus.PENDING,
            recipient_name="Alice",
            recipient_email="alice@example.com",
            certificate_number="CERT-AAAAAAAAAA",
        )
        cert2 = Certificate(
            job_id=job.id,
            row_index=1,
            status=CertificateStatus.INVALID,
            raw_input={"name": ""},
            error_code="VALIDATION_ERROR",
        )
        session.add_all([cert1, cert2])
        session.commit()

        fetched = session.get(CertificateJob, job.id)
        assert fetched is not None
        assert len(fetched.certificates) == 2
        assert [c.row_index for c in fetched.certificates] == [0, 1]
    finally:
        session.close()


def test_job_defaults_are_populated(session_factory):
    session = session_factory()
    try:
        job = _make_job()
        session.add(job)
        session.commit()

        assert job.status == JobStatus.PENDING
        assert job.template_id == "classic_v1"
        assert job.title == "Certificate of Completion"
        assert isinstance(job.created_at, datetime)
        assert isinstance(job.updated_at, datetime)
    finally:
        session.close()


def test_certificate_attempts_default_is_zero(session_factory):
    session = session_factory()
    try:
        job = _make_job()
        session.add(job)
        session.flush()

        cert = Certificate(
            job_id=job.id,
            row_index=0,
            status=CertificateStatus.PENDING,
        )
        session.add(cert)
        session.commit()

        assert cert.attempts == 0
    finally:
        session.close()


def test_unique_job_id_row_index_enforced(session_factory):
    session = session_factory()
    try:
        job = _make_job(total_recipients=2)
        session.add(job)
        session.flush()

        session.add(Certificate(job_id=job.id, row_index=0, status=CertificateStatus.PENDING))
        session.commit()

        session.add(Certificate(job_id=job.id, row_index=0, status=CertificateStatus.PENDING))
        with pytest.raises(IntegrityError):
            session.commit()
    finally:
        session.rollback()
        session.close()


def test_unique_certificate_number_enforced(session_factory):
    session = session_factory()
    try:
        job = _make_job(total_recipients=2)
        session.add(job)
        session.flush()

        session.add(
            Certificate(
                job_id=job.id,
                row_index=0,
                status=CertificateStatus.PENDING,
                certificate_number="CERT-DUPLICATE1",
            )
        )
        session.commit()

        session.add(
            Certificate(
                job_id=job.id,
                row_index=1,
                status=CertificateStatus.PENDING,
                certificate_number="CERT-DUPLICATE1",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
    finally:
        session.rollback()
        session.close()
