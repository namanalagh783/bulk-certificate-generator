"""Tests for the AppError hierarchy (app/errors.py)."""

from app.errors import (
    AppError,
    CertificateNotFound,
    CertificateNotReady,
    InternalError,
    JobNotFinished,
    JobNotFound,
    NoCertificatesAvailable,
    NoValidRecipients,
    TooManyRecipients,
)


def test_job_not_found() -> None:
    err = JobNotFound("abc-123")
    assert isinstance(err, AppError)
    assert err.code == "JOB_NOT_FOUND"
    assert err.http_status == 404
    assert "abc-123" in err.message
    assert err.details is None


def test_certificate_not_found() -> None:
    err = CertificateNotFound("cert-1")
    assert err.code == "CERTIFICATE_NOT_FOUND"
    assert err.http_status == 404
    assert "cert-1" in err.message


def test_certificate_not_ready() -> None:
    err = CertificateNotReady("pending")
    assert err.code == "CERTIFICATE_NOT_READY"
    assert err.http_status == 409
    assert err.details == {"status": "pending"}


def test_job_not_finished() -> None:
    err = JobNotFinished("processing")
    assert err.code == "JOB_NOT_FINISHED"
    assert err.http_status == 409
    assert err.details == {"status": "processing"}


def test_no_certificates_available() -> None:
    err = NoCertificatesAvailable("job-1")
    assert err.code == "NO_CERTIFICATES_AVAILABLE"
    assert err.http_status == 409
    assert "job-1" in err.message


def test_too_many_recipients() -> None:
    err = TooManyRecipients(1000, 1500)
    assert err.code == "TOO_MANY_RECIPIENTS"
    assert err.http_status == 422
    assert err.details == {"max": 1000, "received": 1500}


def test_no_valid_recipients() -> None:
    rows = [{"index": 0, "code": "VALIDATION_ERROR", "errors": []}]
    err = NoValidRecipients(rows)
    assert err.code == "NO_VALID_RECIPIENTS"
    assert err.http_status == 422
    assert err.details == rows


def test_internal_error() -> None:
    err = InternalError("Something went wrong")
    assert err.code == "INTERNAL_ERROR"
    assert err.http_status == 500
    assert err.message == "Something went wrong"
    assert err.details is None


def test_all_are_exceptions() -> None:
    for err in (
        JobNotFound("x"),
        CertificateNotFound("x"),
        CertificateNotReady("pending"),
        JobNotFinished("pending"),
        NoCertificatesAvailable("x"),
        TooManyRecipients(1, 2),
        NoValidRecipients([]),
        InternalError("x"),
    ):
        assert isinstance(err, Exception)
        assert isinstance(err, AppError)
