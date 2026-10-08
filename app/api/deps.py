"""FastAPI dependencies: thin accessors onto `app.state` (ARCHITECTURE §10). No business logic."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.config import Settings
from app.storage.base import CertificateStorage
from app.workers.dispatcher import JobDispatcher


def get_session(request: Request) -> Iterator[Session]:
    """Yield one request-scoped Session from `app.state.session_factory`, closed afterwards.

    One Session per unit of work (CLAUDE.md): each request gets its own, never shared across
    threads or reused across requests.
    """
    session_factory = request.app.state.session_factory
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def get_settings(request: Request) -> Settings:
    """The app's `Settings`, built once at `create_app` time."""
    return request.app.state.settings


def get_dispatcher(request: Request) -> JobDispatcher:
    """The dispatcher wired up in the lifespan (real or injected for tests, ADR-012)."""
    return request.app.state.dispatcher


def get_storage(request: Request) -> CertificateStorage:
    """The storage backend wired up in the lifespan (real or injected for tests, ADR-012)."""
    return request.app.state.storage


# `Annotated` aliases so route signatures read `param: SessionDep` instead of repeating
# `Depends(...)` (and a lint suppression) at every call site (CLAUDE.md Phase 6 clean-up).
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
DispatcherDep = Annotated[JobDispatcher, Depends(get_dispatcher)]
StorageDep = Annotated[CertificateStorage, Depends(get_storage)]
