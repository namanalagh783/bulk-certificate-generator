"""Certificate/job retrieval: single PDF lookup and job ZIP building (ARCHITECTURE §7.4).

No FastAPI imports: these raise `AppError` subclasses and the API layer translates them to HTTP
responses. Everything here takes a `Session`/`CertificateStorage` the caller owns.
"""

import csv
import io
import logging
import os
import tempfile
import zipfile
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.enums import TERMINAL_JOB_STATUSES, CertificateStatus
from app.errors import CertificateNotReady, InternalError, JobNotFinished, NoCertificatesAvailable
from app.models import Certificate
from app.services.job_service import get_certificate, get_job
from app.storage.base import CertificateStorage
from app.utils import slugify_filename

logger = logging.getLogger(__name__)

# Leading characters that a spreadsheet application (Excel/Sheets) would interpret as the start
# of a formula if a cell were opened unescaped (CSV/formula-injection, a local safety choice).
_FORMULA_PREFIXES = ("=", "+", "-", "@")

_MANIFEST_HEADER = [
    "row",
    "name",
    "email",
    "status",
    "certificate_number",
    "file",
    "error_code",
    "error_message",
]


def _neutralise_formula(value: str) -> str:
    """Prefix a cell with "'" when it starts with a formula-triggering character.

    Recipient-supplied text (name, email, error message) ends up in `manifest.csv`; opening that
    file in a spreadsheet app must not let a name like "=cmd()" execute as a formula.
    """
    if value.startswith(_FORMULA_PREFIXES):
        return f"'{value}"
    return value


def open_certificate_file(
    session: Session, storage: CertificateStorage, certificate_id: str
) -> tuple[Path, str]:
    """Resolve a certificate id to (file path, download filename).

    Raises `CertificateNotFound` (via `get_certificate`) for an unknown id, `CertificateNotReady`
    if the certificate hasn't succeeded yet, and `InternalError` if the row says succeeded but the
    file is missing from disk (logged with the certificate id; the client gets a generic message).
    """
    certificate = get_certificate(session, certificate_id)
    if certificate.status != CertificateStatus.SUCCEEDED:
        raise CertificateNotReady(certificate.status)

    file_key = certificate.file_key
    if not file_key or not storage.exists(file_key):
        logger.error(
            "Certificate %s is marked succeeded but its file is missing from storage (file_key=%r)",
            certificate_id,
            file_key,
        )
        raise InternalError("The certificate file is currently unavailable")

    filename = (
        f"{certificate.certificate_number}_{slugify_filename(certificate.recipient_name or '')}.pdf"
    )
    return storage.path_for(file_key), filename


def _zip_entry_name(certificate: Certificate) -> str:
    """ZIP entry name for one succeeded certificate (ADR-016: 0-based `row_index` prefix)."""
    slug = slugify_filename(certificate.recipient_name or "")
    return f"{certificate.row_index:04d}_{slug}_{certificate.certificate_number}.pdf"


def _manifest_csv(certificates: list[Certificate], entry_names: dict[str, str]) -> str:
    """Build `manifest.csv` text covering every recipient row, in `row_index` order."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_MANIFEST_HEADER)
    for certificate in certificates:
        writer.writerow(
            [
                certificate.row_index,
                _neutralise_formula(certificate.recipient_name or ""),
                _neutralise_formula(certificate.recipient_email or ""),
                certificate.status,
                certificate.certificate_number or "",
                entry_names.get(certificate.id, ""),
                certificate.error_code or "",
                _neutralise_formula(certificate.error_message or ""),
            ]
        )
    return buffer.getvalue()


def build_job_zip(session: Session, storage: CertificateStorage, job_id: str) -> tuple[Path, str]:
    """Build a ZIP (succeeded PDFs + `manifest.csv`) for a finished job in a temp file.

    Raises `JobNotFound` (via `get_job`) for an unknown job, `JobNotFinished` if the job hasn't
    reached a terminal status, and `NoCertificatesAvailable` if nothing succeeded. On any
    exception while writing the archive, the temp file is removed before re-raising so a failed
    build never leaks a stray file.
    """
    job = get_job(session, job_id)
    if job.status not in TERMINAL_JOB_STATUSES:
        raise JobNotFinished(job.status)

    certificates = list(
        session.execute(
            sa.select(Certificate)
            .where(Certificate.job_id == job_id)
            .order_by(Certificate.row_index)
        )
        .scalars()
        .all()
    )
    succeeded = [c for c in certificates if c.status == CertificateStatus.SUCCEEDED]
    if not succeeded:
        raise NoCertificatesAvailable(job_id)

    fd, tmp_path_str = tempfile.mkstemp(suffix=".zip")
    tmp_path = Path(tmp_path_str)
    try:
        os.close(fd)  # the ZipFile below reopens the path itself
        entry_names: dict[str, str] = {}
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_STORED) as zf:
            for certificate in succeeded:
                if not certificate.file_key or not storage.exists(certificate.file_key):
                    logger.error(
                        "Certificate %s is marked succeeded but its file is missing from "
                        "storage (file_key=%r)",
                        certificate.id,
                        certificate.file_key,
                    )
                    raise InternalError("One or more certificate files are currently unavailable")
                entry_name = _zip_entry_name(certificate)
                zf.write(storage.path_for(certificate.file_key), arcname=entry_name)
                entry_names[certificate.id] = entry_name

            zf.writestr("manifest.csv", _manifest_csv(certificates, entry_names))
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return tmp_path, f"certificates_{job_id}.zip"
