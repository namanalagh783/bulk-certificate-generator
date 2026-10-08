from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Base, make_engine, make_session_factory
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        storage_dir=tmp_path / "storage",
        dispatcher="inline",
        recover_on_startup=False,
        max_recipients_per_job=50,
    )


@pytest.fixture
def session_factory(settings):
    """Engine + tables created directly (no Alembic) for fast model/service tests."""
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    try:
        yield make_session_factory(engine)
    finally:
        # Disposing releases the file handle so Windows lets tmp_path clean itself up.
        engine.dispose()


@pytest.fixture
def make_client(session_factory):
    """Factory for a `TestClient` built with injected test doubles (ADR-012).

    Tables already exist (via `session_factory`, same DB file) before the app's own lifespan
    opens its engine. Every client built through this fixture is closed at teardown so its
    lifespan (and any dispatcher it owns) shuts down cleanly.
    """
    with ExitStack() as stack:

        def _make(settings, *, dispatcher=None, renderer=None, storage=None):
            app = create_app(settings, dispatcher=dispatcher, renderer=renderer, storage=storage)
            client = stack.enter_context(TestClient(app))
            return client

        yield _make


@pytest.fixture
def client(settings, make_client):
    yield make_client(settings)
