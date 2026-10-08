"""GET job / GET certificates (API_SPEC.md, ARCHITECTURE §7.3)."""

from tests.helpers import ManualDispatcher, make_payload


def _create(client, **kwargs):
    resp = client.post("/api/v1/certificate-jobs", json=make_payload(**kwargs))
    assert resp.status_code == 202
    return resp.json()["job_id"]


def test_job_pending_with_manual_dispatcher(settings, make_client):
    dispatcher = ManualDispatcher()
    client = make_client(settings, dispatcher=dispatcher)

    job_id = _create(client, n_valid=3)
    assert dispatcher.submitted == [job_id]

    resp = client.get(f"/api/v1/certificate-jobs/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "job_id",
        "status",
        "template_id",
        "certificate",
        "progress",
        "created_at",
        "started_at",
        "completed_at",
        "links",
    }
    assert body["status"] == "pending"
    assert body["started_at"] is None
    assert body["completed_at"] is None
    assert body["progress"]["percent_complete"] == 0.0
    assert body["created_at"].endswith("Z")
    assert set(body["certificate"].keys()) == {
        "title",
        "program_name",
        "issuer_name",
        "issue_date",
        "signatory_name",
        "signatory_title",
    }


def test_job_completed_after_processing(settings, make_client):
    dispatcher = ManualDispatcher()
    client = make_client(settings, dispatcher=dispatcher)
    job_id = _create(client, n_valid=3)

    dispatcher.run_all(client.app.state.processor)

    resp = client.get(f"/api/v1/certificate-jobs/{job_id}")
    body = resp.json()
    assert body["status"] == "completed"
    assert body["progress"] == {
        "total": 3,
        "invalid": 0,
        "pending": 0,
        "processing": 0,
        "succeeded": 3,
        "failed": 0,
        "processable": 3,
        "processed": 3,
        "percent_complete": 100.0,
    }
    assert body["started_at"] is not None
    assert body["completed_at"] is not None
    assert body["started_at"].endswith("Z")
    assert body["completed_at"].endswith("Z")


def test_get_unknown_job_returns_404_envelope(client):
    resp = client.get("/api/v1/certificate-jobs/unknown-id")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_list_certificates_unknown_job_returns_404(client):
    resp = client.get("/api/v1/certificate-jobs/unknown-id/certificates")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_list_certificates_body_shape_and_ordering(client):
    job_id = _create(client, n_valid=3)
    resp = client.get(f"/api/v1/certificate-jobs/{job_id}/certificates")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"items", "pagination"}
    assert body["pagination"] == {"limit": 50, "offset": 0, "total": 3}
    assert [item["index"] for item in body["items"]] == [0, 1, 2]

    item = body["items"][0]
    assert set(item.keys()) == {
        "certificate_id",
        "job_id",
        "index",
        "status",
        "recipient",
        "certificate_number",
        "generated_at",
        "error",
        "download_url",
    }
    assert item["status"] == "succeeded"
    assert item["error"] is None
    assert item["download_url"] == f"/api/v1/certificates/{item['certificate_id']}/download"
    assert item["recipient"] == {"name": "Recipient 1", "email": "recipient1@example.com"}


def test_list_certificates_filters_by_status(client):
    job_id = _create(client, n_valid=1, invalid=[{"name": "", "email": "not-an-email"}])
    resp = client.get(f"/api/v1/certificate-jobs/{job_id}/certificates?status=invalid")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["status"] == "invalid"


def test_list_certificates_pagination(client):
    job_id = _create(client, n_valid=5)
    resp = client.get(f"/api/v1/certificate-jobs/{job_id}/certificates?limit=2&offset=1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"] == {"limit": 2, "offset": 1, "total": 5}
    assert [item["index"] for item in body["items"]] == [1, 2]


def test_list_certificates_invalid_status_filter_returns_422_envelope(client):
    job_id = _create(client, n_valid=1)
    resp = client.get(f"/api/v1/certificate-jobs/{job_id}/certificates?status=bogus")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_get_single_certificate(client):
    job_id = _create(client, n_valid=1)
    list_resp = client.get(f"/api/v1/certificate-jobs/{job_id}/certificates")
    certificate_id = list_resp.json()["items"][0]["certificate_id"]

    resp = client.get(f"/api/v1/certificates/{certificate_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["certificate_id"] == certificate_id
    assert body["status"] == "succeeded"


def test_get_unknown_certificate_returns_404_envelope(client):
    resp = client.get("/api/v1/certificates/unknown-id")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CERTIFICATE_NOT_FOUND"
