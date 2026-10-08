"""initial schema: certificate_jobs, certificates

Revision ID: 0001
Revises:
Create Date: 2026-01-01 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "certificate_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("template_id", sa.String(50), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("program_name", sa.String(200), nullable=False),
        sa.Column("issuer_name", sa.String(200), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("signatory_name", sa.String(120), nullable=True),
        sa.Column("signatory_title", sa.String(120), nullable=True),
        sa.Column("total_recipients", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "certificates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("certificate_jobs.id"), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("recipient_name", sa.String(200), nullable=True),
        sa.Column("recipient_email", sa.String(320), nullable=True),
        sa.Column("raw_input", sa.JSON(), nullable=True),
        sa.Column("certificate_number", sa.String(32), nullable=True),
        sa.Column("file_key", sa.String(500), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_details", sa.JSON(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("job_id", "row_index", name="uq_certificates_job_id_row_index"),
        sa.UniqueConstraint("certificate_number", name="uq_certificates_certificate_number"),
    )
    op.create_index(
        "ix_certificates_job_id_status", "certificates", ["job_id", "status"], unique=False
    )
    op.create_index("ix_certificates_job_id", "certificates", ["job_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_certificates_job_id", table_name="certificates")
    op.drop_index("ix_certificates_job_id_status", table_name="certificates")
    op.drop_table("certificates")
    op.drop_table("certificate_jobs")
