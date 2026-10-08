from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_degraded_when_db_unavailable(settings, session_factory):
    """Fault injection via dependency substitution, not a test-only branch in app code."""

    class _RaisingSession:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("db unavailable")

        def close(self):
            pass

    class _RaisingSessionFactory:
        def __call__(self):
            return _RaisingSession()

    with TestClient(create_app(settings)) as c:
        c.app.state.session_factory = _RaisingSessionFactory()
        response = c.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "error"}


def test_settings_defaults():
    # _env_file=None so a developer's local .env can't change the asserted defaults.
    settings = Settings(_env_file=None)
    assert settings.database_url == "sqlite:///./data/certgen.db"
    assert settings.storage_dir == Path("./data/storage")
    assert settings.dispatcher == "thread"
    assert settings.worker_concurrency == 2
    assert settings.max_recipients_per_job == 1000
    assert settings.recover_on_startup is True
    assert settings.log_level == "INFO"
