"""Single-certificate and job-ZIP download routes (API_SPEC.md, ARCHITECTURE §7.4)."""

import csv
import io
import os
import tempfile
import zipfile

from app.models import Certificate
from app.rendering.pdf_renderer import PdfCertificateRenderer
from app.storage.local import LocalFileStorage
from tests.helpers import FailingRenderer, ManualDispatcher, extract_pdf_text


def _payload(recipients: list[dict], **certificate_overrides) -> dict:
    certificate = {
        "program_name": "Intro to Testing",
        "issuer_name": "Example Org",
        "issue_date": "2026-01-15",
        **certificate_overrides,
    }
    return {"certificate": certificate, "recipients": recipients}


def _create(client, recipients, **overrides):
    resp = client.post("/api/v1/certificate-jobs", json=_payload(recipients, **overrides))
    assert resp.status_code == 202
    return resp.json()["job_id"]


def _certificate_id_for(client, job_id, name):
    resp = client.get(f"/api/v1/certificate-jobs/{job_id}/certificates?limit=500")
    items = {item["recipient"]["name"]: item for item in resp.json()["items"]}
    return items[name]["certificate_id"]


def _file_key_for(client, certificate_id):
    with client.app.state.session_factory() as session:
        return session.get(Certificate, certificate_id).file_key


# --------------------------------------------------------------------------------------
# Single certificate download
# --------------------------------------------------------------------------------------


def test_download_certificate_success(client):
    job_id = _create(client, [{"name": "Asha Verma", "email": "asha@example.com"}])
    certificate_id = _certificate_id_for(client, job_id, "Asha Verma")

    resp = client.get(f"/api/v1/certificates/{certificate_id}/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"

    cert_resp = client.get(f"/api/v1/certificates/{certificate_id}")
    certificate_number = cert_resp.json()["certificate_number"]
    expected_filename = f"{certificate_number}_asha-verma.pdf"
    assert resp.headers["content-disposition"] == f'attachment; filename="{expected_filename}"'

    assert resp.content.startswith(b"%PDF")
    assert "Asha Verma" in extract_pdf_text(resp.content)


def test_download_certificate_pending_returns_409(settings, make_client):
    dispatcher = ManualDispatcher()
    client = make_client(settings, dispatcher=dispatcher)
    job_id = _create(client, [{"name": "Recipient 1", "email": "r1@example.com"}])
    certificate_id = _certificate_id_for(client, job_id, "Recipient 1")

    resp = client.get(f"/api/v1/certificates/{certificate_id}/download")
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "CERTIFICATE_NOT_READY"
    assert body["error"]["details"]["status"] == "pending"


def test_download_certificate_failed_returns_409(settings, make_client):
    renderer = FailingRenderer(PdfCertificateRenderer(), fail_for_names={"Fail Me"})
    client = make_client(settings, renderer=renderer)
    job_id = _create(client, [{"name": "Fail Me", "email": "failme@example.com"}])
    certificate_id = _certificate_id_for(client, job_id, "Fail Me")

    resp = client.get(f"/api/v1/certificates/{certificate_id}/download")
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "CERTIFICATE_NOT_READY"
    assert body["error"]["details"]["status"] == "failed"


def test_download_certificate_invalid_returns_409(client):
    job_id = _create(
        client,
        [
            {"name": "Recipient 1", "email": "r1@example.com"},
            {"name": "", "email": "not-an-email"},
        ],
    )
    resp = client.get(f"/api/v1/certificate-jobs/{job_id}/certificates?status=invalid")
    certificate_id = resp.json()["items"][0]["certificate_id"]

    resp = client.get(f"/api/v1/certificates/{certificate_id}/download")
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "CERTIFICATE_NOT_READY"
    assert body["error"]["details"]["status"] == "invalid"


def test_download_unknown_certificate_returns_404(client):
    resp = client.get("/api/v1/certificates/unknown-id/download")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CERTIFICATE_NOT_FOUND"


def test_download_certificate_missing_file_returns_500(settings, make_client):
    storage = LocalFileStorage(settings.storage_dir)
    client = make_client(settings, storage=storage)
    job_id = _create(client, [{"name": "Recipient 1", "email": "r1@example.com"}])
    certificate_id = _certificate_id_for(client, job_id, "Recipient 1")
    file_key = _file_key_for(client, certificate_id)

    os.remove(storage.path_for(file_key))

    resp = client.get(f"/api/v1/certificates/{certificate_id}/download")
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert "traceback" not in body["error"]["message"].lower()


# --------------------------------------------------------------------------------------
# Job ZIP download
# --------------------------------------------------------------------------------------


def test_download_zip_job_not_finished_returns_409(settings, make_client):
    dispatcher = ManualDispatcher()
    client = make_client(settings, dispatcher=dispatcher)
    job_id = _create(client, [{"name": "Recipient 1", "email": "r1@example.com"}])

    resp = client.get(f"/api/v1/certificate-jobs/{job_id}/download")
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "JOB_NOT_FINISHED"
    assert body["error"]["details"]["status"] == "pending"


def test_download_zip_unknown_job_returns_404(client):
    resp = client.get("/api/v1/certificate-jobs/unknown-id/download")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_download_zip_no_certificates_available_returns_409(settings, make_client):
    renderer = FailingRenderer(PdfCertificateRenderer(), fail_for_names={"Recipient 1"})
    client = make_client(settings, renderer=renderer)
    job_id = _create(client, [{"name": "Recipient 1", "email": "r1@example.com"}])

    job_resp = client.get(f"/api/v1/certificate-jobs/{job_id}")
    assert job_resp.json()["status"] == "failed"

    resp = client.get(f"/api/v1/certificate-jobs/{job_id}/download")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "NO_CERTIFICATES_AVAILABLE"


def test_download_zip_success_contents_and_manifest_mixed(settings, make_client):
    renderer = FailingRenderer(PdfCertificateRenderer(), fail_for_names={"Fail Me"})
    client = make_client(settings, renderer=renderer)
    job_id = _create(
        client,
        [
            {"name": "Asha Verma", "email": "asha@example.com"},
            {"name": "Fail Me", "email": "failme@example.com"},
            {"name": "", "email": "not-an-email"},
            {"name": "Asha Verma Dup", "email": "asha@example.com"},
        ],
    )

    list_resp = client.get(f"/api/v1/certificate-jobs/{job_id}/certificates?limit=500")
    items_by_name = {}
    for item in list_resp.json()["items"]:
        items_by_name[item["recipient"]["name"] or item["index"]] = item

    resp = client.get(f"/api/v1/certificate-jobs/{job_id}/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert (
        resp.headers["content-disposition"] == f'attachment; filename="certificates_{job_id}.zip"'
    )

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = set(zf.namelist())

        asha = items_by_name["Asha Verma"]
        expected_entries = {
            f"{asha['index']:04d}_asha-verma_{asha['certificate_number']}.pdf",
            "manifest.csv",
        }
        assert names == expected_entries

        pdf_bytes = zf.read(f"{asha['index']:04d}_asha-verma_{asha['certificate_number']}.pdf")
        assert pdf_bytes.startswith(b"%PDF")

        manifest_text = zf.read("manifest.csv").decode("utf-8")

    rows = list(csv.reader(io.StringIO(manifest_text)))
    assert rows[0] == [
        "row",
        "name",
        "email",
        "status",
        "certificate_number",
        "file",
        "error_code",
        "error_message",
    ]
    data_rows = {int(row[0]): row for row in rows[1:]}
    assert len(data_rows) == 4

    succeeded_row = data_rows[asha["index"]]
    assert succeeded_row[3] == "succeeded"
    assert succeeded_row[5] == f"{asha['index']:04d}_asha-verma_{asha['certificate_number']}.pdf"

    failed_row = data_rows[items_by_name["Fail Me"]["index"]]
    assert failed_row[3] == "failed"
    assert failed_row[6] == "RENDER_ERROR"
    assert failed_row[5] == ""

    invalid_index = next(i for i in data_rows if data_rows[i][3] == "invalid")
    assert data_rows[invalid_index][6] == "VALIDATION_ERROR"

    dup_index = next(i for i in data_rows if data_rows[i][3] == "invalid" and i != invalid_index)
    assert data_rows[dup_index][6] == "DUPLICATE_RECIPIENT"


def test_download_zip_missing_file_returns_500(settings, make_client):
    storage = LocalFileStorage(settings.storage_dir)
    client = make_client(settings, storage=storage)
    job_id = _create(client, [{"name": "Recipient 1", "email": "r1@example.com"}])
    certificate_id = _certificate_id_for(client, job_id, "Recipient 1")
    file_key = _file_key_for(client, certificate_id)

    os.remove(storage.path_for(file_key))

    resp = client.get(f"/api/v1/certificate-jobs/{job_id}/download")
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR"


def test_download_zip_formula_injection_guard(client):
    job_id = _create(client, [{"name": "=Hyperlink Bob", "email": "bob@example.com"}])

    resp = client.get(f"/api/v1/certificate-jobs/{job_id}/download")
    assert resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        manifest_text = zf.read("manifest.csv").decode("utf-8")

    rows = list(csv.reader(io.StringIO(manifest_text)))
    name_cell = rows[1][1]
    assert name_cell == "'=Hyperlink Bob"


def test_temp_zip_cleaned_up_on_success(settings, make_client, tmp_path, monkeypatch):
    temp_dir = tmp_path / "zip_tmp"
    temp_dir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_dir))

    client = make_client(settings)
    job_id = _create(client, [{"name": "Recipient 1", "email": "r1@example.com"}])

    resp = client.get(f"/api/v1/certificate-jobs/{job_id}/download")
    assert resp.status_code == 200
    assert os.listdir(temp_dir) == []


def test_temp_zip_cleaned_up_on_build_failure(settings, make_client, tmp_path, monkeypatch):
    temp_dir = tmp_path / "zip_tmp"
    temp_dir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_dir))

    storage = LocalFileStorage(settings.storage_dir)
    client = make_client(settings, storage=storage)
    job_id = _create(client, [{"name": "Recipient 1", "email": "r1@example.com"}])
    certificate_id = _certificate_id_for(client, job_id, "Recipient 1")
    file_key = _file_key_for(client, certificate_id)
    os.remove(storage.path_for(file_key))

    resp = client.get(f"/api/v1/certificate-jobs/{job_id}/download")
    assert resp.status_code == 500
    assert os.listdir(temp_dir) == []
