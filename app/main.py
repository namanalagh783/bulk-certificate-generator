"""Application factory and lifespan wiring.

Phase 0 only builds the FastAPI app, configures logging and mounts /health. Phase 1 adds the
database engine/session factory to the lifespan; Phase 4 adds the renderer, storage, processor
and dispatcher (ARCHITECTURE §10).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.error_handlers import register_error_handlers
from app.api.routes.certificates import router as certificates_router
from app.api.routes.health import router as health_router
from app.api.routes.jobs import router as jobs_router
from app.config import Settings, get_settings
from app.db import ensure_runtime_dirs, make_engine, make_session_factory
from app.logging_config import configure_logging
from app.rendering.base import CertificateRenderer
from app.rendering.pdf_renderer import PdfCertificateRenderer
from app.storage.base import CertificateStorage
from app.storage.local import LocalFileStorage
from app.workers.dispatcher import InlineDispatcher, JobDispatcher, ThreadPoolDispatcher
from app.workers.processor import JobProcessor
from app.workers.recovery import recover_incomplete_jobs

logger = logging.getLogger(__name__)


def _build_dispatcher(settings: Settings, processor: JobProcessor) -> JobDispatcher:
    """Pick the dispatcher implementation from `settings.dispatcher` (ARCHITECTURE §9)."""
    if settings.dispatcher == "inline":
        return InlineDispatcher(processor.process)
    return ThreadPoolDispatcher(processor.process, settings.worker_concurrency)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hook.

    Builds the engine, session factory, renderer, storage, processor and dispatcher (using
    injected instances from `create_app` when given), stores them on `app.state`, runs startup
    recovery, and shuts the dispatcher down on exit. The app never creates tables itself; that is
    Alembic's job (CLAUDE.md, ARCHITECTURE §9).
    """
    settings: Settings = app.state.settings
    ensure_runtime_dirs(settings)

    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    app.state.engine = engine
    app.state.session_factory = session_factory

    renderer: CertificateRenderer = app.state.injected_renderer or PdfCertificateRenderer()
    storage: CertificateStorage = app.state.injected_storage or LocalFileStorage(
        settings.storage_dir
    )
    app.state.renderer = renderer
    app.state.storage = storage

    processor = JobProcessor(session_factory, renderer, storage)
    app.state.processor = processor

    dispatcher: JobDispatcher = app.state.injected_dispatcher or _build_dispatcher(
        settings, processor
    )
    app.state.dispatcher = dispatcher

    if settings.recover_on_startup:
        recovered_job_ids = recover_incomplete_jobs(session_factory)
        logger.info("Startup recovery re-submitting %d job(s)", len(recovered_job_ids))
        for job_id in recovered_job_ids:
            dispatcher.submit(job_id)

    yield

    dispatcher.shutdown()
    engine.dispose()


def create_app(
    settings: Settings | None = None,
    *,
    dispatcher: JobDispatcher | None = None,
    renderer: CertificateRenderer | None = None,
    storage: CertificateStorage | None = None,
) -> FastAPI:
    """Build the FastAPI application, wiring settings and optional test doubles onto `app.state`."""
    if settings is None:
        settings = get_settings()

    configure_logging(settings.log_level)

    app = FastAPI(title="Bulk Certificate Generator", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.injected_dispatcher = dispatcher
    app.state.injected_renderer = renderer
    app.state.injected_storage = storage

    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(jobs_router)
    app.include_router(certificates_router)

    return app


app = create_app()
