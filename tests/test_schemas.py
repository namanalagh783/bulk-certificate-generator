"""Tests for app/schemas.py: input validation and output serialisation."""

from datetime import date, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.schemas import CertificateInfoIn, JobCreateRequest, JobOut, LinksOut


def _certificate_payload(**overrides: object) -> dict:
    payload = {
        "program_name": "Advanced Python Bootcamp",
        "issuer_name": "Acme Academy",
        "issue_date": "2026-10-01",
    }
    payload.update(overrides)
    return payload


class TestCertificateInfoIn:
    def test_defaults_title(self) -> None:
        info = CertificateInfoIn.model_validate(_certificate_payload())
        assert info.title == "Certificate of Completion"

    def test_trims_strings(self) -> None:
        info = CertificateInfoIn.model_validate(
            _certificate_payload(program_name="  Python Bootcamp  ")
        )
        assert info.program_name == "Python Bootcamp"

    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            CertificateInfoIn.model_validate(_certificate_payload(extra_field="nope"))

    def test_rejects_empty_program_name(self) -> None:
        with pytest.raises(ValidationError):
            CertificateInfoIn.model_validate(_certificate_payload(program_name=""))

    def test_rejects_empty_issuer_name(self) -> None:
        with pytest.raises(ValidationError):
            CertificateInfoIn.model_validate(_certificate_payload(issuer_name=""))

    def test_rejects_bad_date(self) -> None:
        with pytest.raises(ValidationError):
            CertificateInfoIn.model_validate(_certificate_payload(issue_date="not-a-date"))

    def test_rejects_signatory_over_120_chars(self) -> None:
        with pytest.raises(ValidationError):
            CertificateInfoIn.model_validate(_certificate_payload(signatory_name="x" * 121))

    def test_empty_signatory_becomes_none(self) -> None:
        info = CertificateInfoIn.model_validate(
            _certificate_payload(signatory_name="   ", signatory_title="")
        )
        assert info.signatory_name is None
        assert info.signatory_title is None

    def test_issue_date_parsed_as_date(self) -> None:
        info = CertificateInfoIn.model_validate(_certificate_payload())
        assert info.issue_date == date(2026, 10, 1)


class TestJobCreateRequest:
    def test_empty_recipients_rejected(self) -> None:
        with pytest.raises(ValidationError):
            JobCreateRequest.model_validate(
                {"certificate": _certificate_payload(), "recipients": []}
            )

    def test_non_list_recipients_rejected(self) -> None:
        with pytest.raises(ValidationError):
            JobCreateRequest.model_validate(
                {"certificate": _certificate_payload(), "recipients": "not-a-list"}
            )

    def test_large_recipient_list_accepted_by_schema(self) -> None:
        # The envelope schema has no max_length; MAX_RECIPIENTS_PER_JOB is enforced in the
        # service so it can return TOO_MANY_RECIPIENTS instead of a generic VALIDATION_ERROR
        # (ADR-018).
        recipients = [{"name": f"Person {i}", "email": f"p{i}@example.com"} for i in range(1001)]
        request = JobCreateRequest.model_validate(
            {"certificate": _certificate_payload(), "recipients": recipients}
        )
        assert len(request.recipients) == 1001

    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            JobCreateRequest.model_validate(
                {
                    "certificate": _certificate_payload(),
                    "recipients": [{"name": "A", "email": "a@example.com"}],
                    "extra": 1,
                }
            )


class TestUtcDateTime:
    def _job_out_kwargs(self, **overrides: object) -> dict:
        kwargs = {
            "job_id": "j1",
            "status": "pending",
            "template_id": "classic_v1",
            "certificate": {
                "title": "Certificate of Completion",
                "program_name": "Python",
                "issuer_name": "Acme",
                "issue_date": date(2026, 10, 1),
                "signatory_name": None,
                "signatory_title": None,
            },
            "progress": {
                "total": 1,
                "invalid": 0,
                "pending": 1,
                "processing": 0,
                "succeeded": 0,
                "failed": 0,
                "processable": 1,
                "processed": 0,
                "percent_complete": 0.0,
            },
            "created_at": datetime(2026, 10, 8, 10, 0, 0),
            "started_at": None,
            "completed_at": None,
            "links": {
                "self": "/api/v1/certificate-jobs/j1",
                "certificates": "/api/v1/certificate-jobs/j1/certificates",
                "download": "/api/v1/certificate-jobs/j1/download",
            },
        }
        kwargs.update(overrides)
        return kwargs

    def test_naive_datetime_serialises_as_utc_z(self) -> None:
        job = JobOut.model_validate(self._job_out_kwargs())
        dumped = job.model_dump(mode="json", by_alias=True)
        assert dumped["created_at"] == "2026-10-08T10:00:00Z"

    def test_aware_datetime_converted_to_utc(self) -> None:
        # 17:00 at UTC+5 offset is equivalent to 12:00Z.
        from datetime import timezone

        plus_five = timezone(timedelta(hours=5))
        aware_local = datetime(2026, 10, 8, 17, 0, 0, tzinfo=plus_five)
        job = JobOut.model_validate(self._job_out_kwargs(created_at=aware_local))
        dumped = job.model_dump(mode="json", by_alias=True)
        assert dumped["created_at"] == "2026-10-08T12:00:00Z"

    def test_none_stays_null(self) -> None:
        job = JobOut.model_validate(self._job_out_kwargs())
        dumped = job.model_dump(mode="json", by_alias=True)
        assert dumped["started_at"] is None
        assert dumped["completed_at"] is None

    def test_links_serialise_with_key_self(self) -> None:
        links = LinksOut.model_validate(
            {
                "self": "/api/v1/certificate-jobs/j1",
                "certificates": "/api/v1/certificate-jobs/j1/certificates",
                "download": "/api/v1/certificate-jobs/j1/download",
            }
        )
        dumped = links.model_dump(mode="json", by_alias=True)
        assert dumped["self"] == "/api/v1/certificate-jobs/j1"
        assert "self_" not in dumped
