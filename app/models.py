"""CertificateJob and Certificate ORM models (ARCHITECTURE §4).

Status columns are `String(32)` rather than SQLAlchemy `Enum` so the schema stays portable
between SQLite and Postgres (no dialect-specific CHECK/ENUM type); the Python side still works
with the `JobStatus`/`CertificateStatus` StrEnum members.
"""

import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.enums import JobStatus
from app.utils import utcnow


def _new_uuid() -> str:
    """Server-generated primary key; never derived from user input (CLAUDE.md)."""
    return str(uuid.uuid4())


class CertificateJob(Base):
    """A single bulk-generation request: shared certificate details plus overall status."""

    __tablename__ = "certificate_jobs"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=_new_uuid)
    status: Mapped[str] = mapped_column(sa.String(32), default=JobStatus.PENDING, nullable=False)
    template_id: Mapped[str] = mapped_column(sa.String(50), default="classic_v1", nullable=False)
    title: Mapped[str] = mapped_column(
        sa.String(100), default="Certificate of Completion", nullable=False
    )
    program_name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    issuer_name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    issue_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    signatory_name: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    signatory_title: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    total_recipients: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    last_error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    certificates: Mapped[list["Certificate"]] = relationship(
        "Certificate",
        back_populates="job",
        order_by="Certificate.row_index",
    )


class Certificate(Base):
    """One row per submitted recipient (valid or invalid) for a job."""

    __tablename__ = "certificates"
    __table_args__ = (
        sa.UniqueConstraint("job_id", "row_index", name="uq_certificates_job_id_row_index"),
        sa.Index("ix_certificates_job_id_status", "job_id", "status"),
        sa.UniqueConstraint("certificate_number", name="uq_certificates_certificate_number"),
    )

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=_new_uuid)
    job_id: Mapped[str] = mapped_column(
        sa.String(36), sa.ForeignKey("certificate_jobs.id"), nullable=False, index=True
    )
    row_index: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    recipient_name: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    recipient_email: Mapped[str | None] = mapped_column(sa.String(320), nullable=True)
    raw_input: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        sa.JSON, nullable=True
    )
    certificate_number: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    file_key: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    file_size: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    error_details: Mapped[dict | list | None] = mapped_column(sa.JSON, nullable=True)
    attempts: Mapped[int] = mapped_column(sa.Integer, default=0, server_default="0", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    generated_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    job: Mapped["CertificateJob"] = relationship("CertificateJob", back_populates="certificates")
