"""Routes for /api/v1/certificates (ARCHITECTURE §7.3, §7.4)."""

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.api.deps import SessionDep, StorageDep
from app.api.presenters import certificate_out
from app.schemas import CertificateOut, ErrorEnvelope
from app.services import job_service, retrieval

router = APIRouter(prefix="/api/v1/certificates", tags=["certificates"])


@router.get(
    "/{certificate_id}",
    response_model=CertificateOut,
    responses={404: {"model": ErrorEnvelope}},
)
def get_certificate(certificate_id: str, session: SessionDep) -> CertificateOut:
    """A single item in the same shape as a certificate-list entry (API_SPEC.md)."""
    certificate = job_service.get_certificate(session, certificate_id)
    return certificate_out(certificate)


@router.get(
    "/{certificate_id}/download",
    responses={
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
    },
)
def download_certificate(
    certificate_id: str, session: SessionDep, storage: StorageDep
) -> FileResponse:
    """The generated PDF for one succeeded certificate (ARCHITECTURE §7.4)."""
    path, filename = retrieval.open_certificate_file(session, storage, certificate_id)
    return FileResponse(path, media_type="application/pdf", filename=filename)
