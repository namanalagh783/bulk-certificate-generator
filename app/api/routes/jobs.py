"""Routes for /api/v1/certificate-jobs (ARCHITECTURE §7.1, §7.3).

Handlers hold no business logic: they call `app.services.job_service`, dispatch after the
commit, and shape the response via `app.api.presenters` (ADR-019).
"""

from typing import Annotated

from fastapi import APIRouter, Query, Response
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.api.deps import DispatcherDep, SessionDep, SettingsDep, StorageDep
from app.api.presenters import certificate_out, job_created_out, job_out
from app.enums import CertificateStatus
from app.schemas import (
    CertificateListOut,
    ErrorEnvelope,
    JobCreatedOut,
    JobCreateRequest,
    JobOut,
    PaginationOut,
)
from app.services import job_service, retrieval

router = APIRouter(prefix="/api/v1/certificate-jobs", tags=["certificate-jobs"])


@router.post(
    "",
    status_code=202,
    response_model=JobCreatedOut,
    responses={422: {"model": ErrorEnvelope}},
)
def create_job(
    body: JobCreateRequest,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    dispatcher: DispatcherDep,
) -> JobCreatedOut:
    """Validate, persist, then dispatch (ARCHITECTURE §7.1 steps 2-7)."""
    created = job_service.create_job(session, body, settings)
    # Dispatch AFTER the commit so a worker thread never races the writer for the new rows.
    dispatcher.submit(created.job_id)
    response.headers["Location"] = f"/api/v1/certificate-jobs/{created.job_id}"
    return job_created_out(created)


@router.get(
    "/{job_id}",
    response_model=JobOut,
    responses={404: {"model": ErrorEnvelope}},
)
def get_job(job_id: str, session: SessionDep) -> JobOut:
    """Job status and progress (ARCHITECTURE §7.3)."""
    job = job_service.get_job(session, job_id)
    progress = job_service.get_progress(session, job_id)
    return job_out(job, progress)


@router.get(
    "/{job_id}/certificates",
    response_model=CertificateListOut,
    responses={404: {"model": ErrorEnvelope}, 422: {"model": ErrorEnvelope}},
)
def list_certificates(
    job_id: str,
    session: SessionDep,
    status: CertificateStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CertificateListOut:
    """Per-recipient results, filtered and paginated (ARCHITECTURE §7.3).

    `status` is typed as `CertificateStatus | None`: an unrecognised value fails FastAPI's own
    query-parameter validation and is converted to the 422 envelope by `error_handlers.py`.
    """
    items, total = job_service.list_certificates(session, job_id, status, limit, offset)
    return CertificateListOut(
        items=[certificate_out(c) for c in items],
        pagination=PaginationOut(limit=limit, offset=offset, total=total),
    )


@router.get(
    "/{job_id}/download",
    responses={
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
    },
)
def download_job_zip(job_id: str, session: SessionDep, storage: StorageDep) -> FileResponse:
    """Succeeded PDFs + `manifest.csv` as a ZIP (ARCHITECTURE §7.4).

    The temp file is removed by a `BackgroundTask` that runs after the response is sent, whether
    or not the client fully read it (ADR-009).
    """
    temp_path, filename = retrieval.build_job_zip(session, storage, job_id)
    return FileResponse(
        temp_path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(temp_path.unlink, missing_ok=True),
    )
