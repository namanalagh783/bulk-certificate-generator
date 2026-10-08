"""POST /api/v1/certificate-jobs (API_SPEC.md, ARCHITECTURE §7.1)."""

from app.models import CertificateJob
from tests.helpers import make_payload


def test_create_job_returns_202_with_location_and_exact_body(client, session_factory):
    resp = client.post("/api/v1/certificate-jobs", json=make_payload(n_valid=2))

    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    assert resp.headers["location"] == f"/api/v1/certificate-jobs/{job_id}"

    body = resp.json()
    assert set(body.keys()) == {"job_id", "status", "summary", "invalid_recipients", "links"}
    assert body["status"] == "pending"
    assert body["summary"] == {"total": 2, "accepted": 2, "invalid": 0}
    assert body["invalid_recipients"] == []
    assert body["links"] == {
        "self": f"/api/v1/certificate-jobs/{job_id}",
        "certificates": f"/api/v1/certificate-jobs/{job_id}/certificates",
        "download": f"/api/v1/certificate-jobs/{job_id}/download",
    }

    # With the default InlineDispatcher (settings fixture) the job already ran synchronously
    # inside the POST, but the acceptance snapshot is always "pending" regardless (API_SPEC.md).
    with session_factory() as session:
        job = session.get(CertificateJob, job_id)
        assert job is not None


def test_mixed_valid_invalid_shapes_invalid_rows(client):
    payload = make_payload(
        n_valid=1,
        invalid=[
            123,
            {"name": "Dup", "email": "recipient1@example.com"},
            {"name": "", "email": "not-an-email"},
        ],
    )
    resp = client.post("/api/v1/certificate-jobs", json=payload)

    assert resp.status_code == 202
    body = resp.json()
    assert body["summary"] == {"total": 4, "accepted": 1, "invalid": 3}

    by_index = {row["index"]: row for row in body["invalid_recipients"]}
    assert set(by_index) == {1, 2, 3}

    non_object_row = by_index[1]
    assert non_object_row["code"] == "VALIDATION_ERROR"
    assert non_object_row["errors"][0]["field"] is None

    dup_row = by_index[2]
    assert dup_row["code"] == "DUPLICATE_RECIPIENT"
    assert "index 0" in dup_row["errors"][0]["message"]

    invalid_row = by_index[3]
    assert invalid_row["code"] == "VALIDATION_ERROR"
    fields = {e["field"] for e in invalid_row["errors"]}
    assert "name" in fields or "email" in fields


def test_all_invalid_returns_422_no_valid_recipients_and_no_job_created(client, session_factory):
    payload = make_payload(n_valid=0, invalid=[{"name": "", "email": "not-an-email"}])
    resp = client.post("/api/v1/certificate-jobs", json=payload)

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "NO_VALID_RECIPIENTS"
    assert isinstance(body["error"]["details"], list)
    assert body["error"]["details"][0]["index"] == 0

    with session_factory() as session:
        assert session.query(CertificateJob).count() == 0


def _assert_validation_error(resp):
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert isinstance(body["error"]["details"], list)
    for item in body["error"]["details"]:
        assert set(item.keys()) == {"loc", "message"}


def test_missing_certificate_is_validation_error(client):
    payload = make_payload()
    del payload["certificate"]
    _assert_validation_error(client.post("/api/v1/certificate-jobs", json=payload))


def test_bad_issue_date_is_validation_error(client):
    payload = make_payload(issue_date="not-a-date")
    _assert_validation_error(client.post("/api/v1/certificate-jobs", json=payload))


def test_empty_recipients_is_validation_error(client):
    payload = make_payload()
    payload["recipients"] = []
    _assert_validation_error(client.post("/api/v1/certificate-jobs", json=payload))


def test_recipients_not_a_list_is_validation_error(client):
    payload = make_payload()
    payload["recipients"] = "not-a-list"
    _assert_validation_error(client.post("/api/v1/certificate-jobs", json=payload))


def test_extra_top_level_field_is_validation_error(client):
    payload = make_payload()
    payload["unexpected"] = "nope"
    _assert_validation_error(client.post("/api/v1/certificate-jobs", json=payload))


def test_extra_certificate_field_is_validation_error(client):
    payload = make_payload()
    payload["certificate"]["unexpected"] = "nope"
    _assert_validation_error(client.post("/api/v1/certificate-jobs", json=payload))


def test_too_many_recipients_returns_422(client):
    # The `settings` fixture sets max_recipients_per_job=50.
    payload = make_payload(n_valid=51)
    resp = client.post("/api/v1/certificate-jobs", json=payload)

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "TOO_MANY_RECIPIENTS"
    assert body["error"]["details"] == {"max": 50, "received": 51}
