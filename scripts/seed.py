"""Idempotent demo seed data (ADR-020).

Run on every container start (after migrations, before uvicorn) so a fresh `docker compose up`
has demo jobs — and their rendered PDFs — ready to look at immediately. Idempotent: if the
database already has ANY `CertificateJob`, this does nothing, so restarting the container never
creates duplicate jobs.

This is a demo/ops script (not part of the `app` package), so plain `print` is fine here
(CLAUDE.md allows `print` in scripts/). It goes through the real `job_service.create_job` +
`JobProcessor` code paths rather than raw SQL inserts, so the seeded jobs can never drift from
how the app actually behaves.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db import Base, ensure_runtime_dirs, make_engine, make_session_factory
from app.models import CertificateJob
from app.rendering.base import CertificateRenderer
from app.rendering.pdf_renderer import PdfCertificateRenderer
from app.schemas import JobCreateRequest
from app.services import job_service
from app.storage.base import CertificateStorage
from app.storage.local import LocalFileStorage
from app.workers.processor import JobProcessor

# Repo root is two parents up from this file (scripts/seed.py -> scripts -> repo root), so the
# sample paths resolve correctly regardless of the caller's current working directory (including
# inside the container, where WORKDIR is /app).
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SAMPLE_FILES = [
    REPO_ROOT / "samples" / "sample_request.json",
    REPO_ROOT / "samples" / "sample_request_with_errors.json",
]


def _already_seeded(session: Session) -> bool:
    """Whether any job already exists; the seed is a no-op once this is true."""
    return session.query(CertificateJob).first() is not None


def seed(
    session_factory: sessionmaker[Session],
    settings: Settings,
    renderer: CertificateRenderer,
    storage: CertificateStorage,
    sample_files: list[Path],
) -> list[str]:
    """Create and process one job per `sample_files` entry, unless jobs already exist.

    Returns the new job ids, or `[]` if seeding was skipped because the database was non-empty.
    Uses the same `create_job` / `JobProcessor` code the API uses, so a seeded job is
    indistinguishable from one submitted by a real client.
    """
    with session_factory() as session:
        if _already_seeded(session):
            return []

        job_ids: list[str] = []
        for sample_file in sample_files:
            payload = json.loads(sample_file.read_text(encoding="utf-8"))
            request = JobCreateRequest.model_validate(payload)
            created = job_service.create_job(session, request, settings)
            job_ids.append(created.job_id)

    # Process synchronously (no dispatcher involved) so the seeded PDFs exist before uvicorn
    # starts serving traffic. Each job is processed in its own call, matching how a real
    # dispatcher hands off one job id at a time.
    processor = JobProcessor(session_factory, renderer, storage)
    for job_id in job_ids:
        processor.process(job_id)

    return job_ids


def _print_job_summary(session_factory: sessionmaker[Session], job_id: str, base_url: str) -> None:
    """Print one job's id, status and succeeded/invalid counts, plus its API URLs."""
    with session_factory() as session:
        job = job_service.get_job(session, job_id)
        progress = job_service.get_progress(session, job_id)

    job_url = f"{base_url}/api/v1/certificate-jobs/{job_id}"
    print(f"  job_id: {job_id}")
    print(f"    status: {job.status}  succeeded: {progress.succeeded}  invalid: {progress.invalid}")
    print(f"    {job_url}")
    print(f"    {job_url}/certificates")
    print(f"    {job_url}/download")


def main() -> int:
    """Build runtime dependencies from settings, seed, print a summary, and exit cleanly."""
    settings = get_settings()
    ensure_runtime_dirs(settings)

    engine = make_engine(settings.database_url)
    try:
        # Seeding can run against a freshly migrated (but not-yet-ORM-touched) database; the
        # tables already exist via Alembic (run immediately before this script in the compose
        # command), so this call is a no-op in practice but keeps the script runnable standalone.
        Base.metadata.create_all(engine, checkfirst=True)
        session_factory = make_session_factory(engine)

        renderer = PdfCertificateRenderer()
        storage = LocalFileStorage(settings.storage_dir)

        job_ids = seed(session_factory, settings, renderer, storage, DEFAULT_SAMPLE_FILES)

        if not job_ids:
            print("Database already contains jobs — skipping seed.")
            return 0

        print(f"Seeded {len(job_ids)} demo job(s):")
        for job_id in job_ids:
            _print_job_summary(session_factory, job_id, "http://localhost:8000")
        return 0
    except Exception as exc:  # noqa: BLE001 - top-level script guard, print and exit non-zero
        print(f"Seeding failed: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
