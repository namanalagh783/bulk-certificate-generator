"""Tests for scripts/seed.py (ADR-020): idempotent demo seed data.

scripts/ is not an importable package (no __init__.py, not part of app/), so the module is loaded
by file path via importlib, matching how it is actually invoked (`python scripts/seed.py`).
"""

import importlib.util
from pathlib import Path

import sqlalchemy as sa

from app.enums import CertificateStatus, JobStatus
from app.models import Certificate, CertificateJob
from app.rendering.pdf_renderer import PdfCertificateRenderer
from app.schemas import JobCreateRequest
from app.services import job_service
from app.storage.local import LocalFileStorage

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = REPO_ROOT / "scripts" / "seed.py"
SAMPLE_FILES = [
    REPO_ROOT / "samples" / "sample_request.json",
    REPO_ROOT / "samples" / "sample_request_with_errors.json",
]


def _load_seed_module():
    spec = importlib.util.spec_from_file_location("seed_script", SEED_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed_module = _load_seed_module()


def _job_statuses(session_factory):
    with session_factory() as session:
        return [job.status for job in session.query(CertificateJob).all()]


def test_seed_creates_two_jobs_with_rendered_files(session_factory, settings):
    renderer = PdfCertificateRenderer()
    storage = LocalFileStorage(settings.storage_dir)

    job_ids = seed_module.seed(session_factory, settings, renderer, storage, SAMPLE_FILES)

    assert len(job_ids) == 2
    statuses = sorted(_job_statuses(session_factory))
    assert statuses == sorted([JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_ERRORS])

    with session_factory() as session:
        succeeded = (
            session.execute(
                sa.select(Certificate).where(Certificate.status == CertificateStatus.SUCCEEDED)
            )
            .scalars()
            .all()
        )
        assert succeeded  # at least one succeeded certificate across the two seeded jobs
        for certificate in succeeded:
            assert storage.exists(certificate.file_key)


def test_seed_is_idempotent(session_factory, settings):
    renderer = PdfCertificateRenderer()
    storage = LocalFileStorage(settings.storage_dir)

    first = seed_module.seed(session_factory, settings, renderer, storage, SAMPLE_FILES)
    assert len(first) == 2

    second = seed_module.seed(session_factory, settings, renderer, storage, SAMPLE_FILES)
    assert second == []

    with session_factory() as session:
        count = session.query(CertificateJob).count()
    assert count == 2


def test_seed_skips_when_a_job_already_exists(session_factory, settings):
    """Even a single pre-existing job (created outside the seed, e.g. by a real request) is
    enough to skip seeding entirely."""
    renderer = PdfCertificateRenderer()
    storage = LocalFileStorage(settings.storage_dir)

    with session_factory() as session:
        payload = {
            "certificate": {
                "title": "Certificate of Completion",
                "program_name": "Pre-existing Program",
                "issuer_name": "Acme Academy",
                "issue_date": "2026-01-01",
            },
            "recipients": [{"name": "Existing Person", "email": "existing@example.com"}],
        }
        request = JobCreateRequest.model_validate(payload)
        job_service.create_job(session, request, settings)

    result = seed_module.seed(session_factory, settings, renderer, storage, SAMPLE_FILES)

    assert result == []
    with session_factory() as session:
        count = session.query(CertificateJob).count()
    assert count == 1
