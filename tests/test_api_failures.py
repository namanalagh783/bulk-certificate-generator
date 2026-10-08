"""One recipient's render fails; the rest still succeed (ADR-015)."""

from app.models import Certificate
from app.rendering.pdf_renderer import PdfCertificateRenderer
from app.storage.local import LocalFileStorage
from tests.helpers import FailingRenderer, make_payload


def test_render_failure_for_one_recipient_others_succeed(settings, make_client):
    storage = LocalFileStorage(settings.storage_dir)
    renderer = FailingRenderer(PdfCertificateRenderer(), fail_for_names={"Fail Me"})
    client = make_client(settings, renderer=renderer, storage=storage)

    payload = make_payload(n_valid=2)
    payload["recipients"].append({"name": "Fail Me", "email": "failme@example.com"})
    resp = client.post("/api/v1/certificate-jobs", json=payload)
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    job_resp = client.get(f"/api/v1/certificate-jobs/{job_id}")
    assert job_resp.json()["status"] == "completed_with_errors"

    list_resp = client.get(f"/api/v1/certificate-jobs/{job_id}/certificates")
    items = {item["recipient"]["name"]: item for item in list_resp.json()["items"]}

    failed = items["Fail Me"]
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "RENDER_ERROR"
    assert failed["download_url"] is None

    for name in ("Recipient 1", "Recipient 2"):
        succeeded = items[name]
        assert succeeded["status"] == "succeeded"
        assert succeeded["download_url"] is not None
        assert succeeded["error"] is None

        certificate_id = succeeded["certificate_id"]
        with client.app.state.session_factory() as session:
            file_key = session.get(Certificate, certificate_id).file_key
        assert storage.exists(file_key)

        # ADR-015: Phase 6 adds the actual download assertion for this scenario.
        download_resp = client.get(f"/api/v1/certificates/{certificate_id}/download")
        assert download_resp.status_code == 200
        assert download_resp.content.startswith(b"%PDF")
