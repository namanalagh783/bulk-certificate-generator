"""Engine/session factory and declarative Base.

This module owns everything SQLAlchemy needs to connect to the database. It is imported by both
the app lifespan (app/main.py) and alembic/env.py so the two never drift: the same
`ensure_runtime_dirs`/`make_engine` logic is used for a live app and for running migrations.
"""

from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Settings


class Base(DeclarativeBase):
    """Shared declarative base; all ORM models inherit from this."""


def _sqlite_db_path(url: str) -> Path | None:
    """Return the on-disk file path for a `sqlite:///...` URL, or None for `:memory:`/non-SQLite."""
    if not url.startswith("sqlite:///"):
        return None
    path_part = url.removeprefix("sqlite:///")
    if path_part == ":memory:" or not path_part:
        return None
    return Path(path_part)


def make_engine(url: str) -> Engine:
    """Build an Engine appropriate for `url`.

    SQLite needs three things a request-serving process with background worker threads relies
    on (ARCHITECTURE §8): `check_same_thread=False` (the session that writes is not necessarily
    the session that opened the connection), WAL mode (readers don't block writers) and a busy
    timeout (retry briefly instead of raising "database is locked"). Foreign keys are off by
    default in SQLite, so they're turned on to match Postgres's always-on behaviour. Non-SQLite
    URLs (Postgres) get a plain engine with pre-ping so stale pooled connections are detected.
    """
    if url.startswith("sqlite"):
        db_path = _sqlite_db_path(url)
        if db_path is not None:
            db_path.parent.mkdir(parents=True, exist_ok=True)

        engine = create_engine(url, connect_args={"check_same_thread": False})

        @sa.event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine

    return create_engine(url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build a session factory bound to `engine`.

    `expire_on_commit=False`: handlers and services commonly read attributes off an ORM object
    right after committing it (e.g. to build a response) before the session is closed. Expiring
    on commit would force an extra round-trip to re-fetch those attributes for no benefit here,
    since each unit of work uses one short-lived session that is closed immediately after.
    """
    return sessionmaker(bind=engine, expire_on_commit=False)


def ensure_runtime_dirs(settings: Settings) -> None:
    """Create the SQLite parent directory and `storage_dir` if missing.

    The single helper used by both the app lifespan and alembic/env.py (ARCHITECTURE §9), so
    "where do runtime directories get created" has exactly one answer.
    """
    db_path = _sqlite_db_path(settings.database_url)
    if db_path is not None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
