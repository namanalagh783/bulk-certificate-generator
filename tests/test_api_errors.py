"""Envelope for unknown routes, wrong methods, and unhandled exceptions (ADR-019)."""

from fastapi.testclient import TestClient

from app.main import create_app
from tests.helpers import make_payload


def test_unknown_route_returns_404_envelope(client):
    resp = client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_wrong_method_returns_405_envelope(client):
    resp = client.delete("/api/v1/certificate-jobs")
    assert resp.status_code == 405
    assert resp.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_unhandled_exception_returns_500_envelope_without_internals(settings):
    class BrokenDispatcher:
        def submit(self, job_id: str) -> None:
            raise RuntimeError("boom: secret internal detail")

        def shutdown(self) -> None:
            pass

    app = create_app(settings, dispatcher=BrokenDispatcher())
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/api/v1/certificate-jobs", json=make_payload(n_valid=1))

    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert "boom" not in body["error"]["message"]
    assert "secret" not in body["error"]["message"]
