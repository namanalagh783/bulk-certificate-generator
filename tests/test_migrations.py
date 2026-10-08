"""Verifies alembic/env.py and the 0001_initial migration against a real temp SQLite file.

Runs the actual Alembic Config (not Base.metadata.create_all) so a mistake in the migration
itself -- as opposed to in the ORM models -- would be caught.
"""

from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command

REPO_ROOT = Path(__file__).resolve().parent.parent


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_upgrade_and_downgrade(tmp_path):
    db_path = tmp_path / "migration_test.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    cfg = _alembic_config(db_url)

    command.upgrade(cfg, "head")

    engine = sa.create_engine(db_url)
    try:
        inspector = sa.inspect(engine)
        tables = set(inspector.get_table_names())
        assert {"certificate_jobs", "certificates"} <= tables

        unique_names = {uc["name"] for uc in inspector.get_unique_constraints("certificates")}
        assert "uq_certificates_job_id_row_index" in unique_names
        assert "uq_certificates_certificate_number" in unique_names

        index_names = {ix["name"] for ix in inspector.get_indexes("certificates")}
        assert "ix_certificates_job_id_status" in index_names
    finally:
        engine.dispose()

    command.downgrade(cfg, "base")

    engine = sa.create_engine(db_url)
    try:
        inspector = sa.inspect(engine)
        tables = set(inspector.get_table_names())
        assert "certificate_jobs" not in tables
        assert "certificates" not in tables
    finally:
        engine.dispose()
