from pathlib import Path

import pytest

from app.storage.base import StorageError, certificate_key
from app.storage.local import LocalFileStorage


def test_certificate_key_format():
    assert certificate_key("job-1", "cert-1") == "certificates/job-1/cert-1.pdf"


def test_save_and_read_round_trip(tmp_path: Path):
    storage = LocalFileStorage(tmp_path)
    key = certificate_key("job-1", "cert-1")

    size = storage.save(key, b"hello world")

    assert size == len(b"hello world")
    assert storage.exists(key)
    assert storage.path_for(key).read_bytes() == b"hello world"


def test_save_returns_number_of_bytes_written(tmp_path: Path):
    storage = LocalFileStorage(tmp_path)
    data = b"x" * 12345
    size = storage.save(certificate_key("job-1", "cert-1"), data)
    assert size == 12345


def test_save_creates_parent_directories(tmp_path: Path):
    storage = LocalFileStorage(tmp_path)
    key = certificate_key("new-job", "new-cert")
    storage.save(key, b"data")
    assert (tmp_path / "certificates" / "new-job" / "new-cert.pdf").is_file()


def test_save_leaves_no_temp_files_behind(tmp_path: Path):
    storage = LocalFileStorage(tmp_path)
    storage.save(certificate_key("job-1", "cert-1"), b"data")
    leftovers = list((tmp_path / "certificates" / "job-1").glob(".tmp-*"))
    assert leftovers == []


@pytest.mark.parametrize(
    "bad_key",
    [
        "",
        "/etc/passwd",
        "../secrets.pdf",
        "certificates/../../secrets.pdf",
        "certificates\\job-1\\cert-1.pdf",
        "C:/Windows/system.pdf",
        "C:\\Windows\\system.pdf",
    ],
)
def test_rejects_invalid_keys(tmp_path: Path, bad_key: str):
    storage = LocalFileStorage(tmp_path)
    with pytest.raises(StorageError):
        storage.save(bad_key, b"data")
    with pytest.raises(StorageError):
        storage.path_for(bad_key)
    with pytest.raises(StorageError):
        storage.exists(bad_key)


def test_exists_false_for_missing_file(tmp_path: Path):
    storage = LocalFileStorage(tmp_path)
    assert storage.exists(certificate_key("job-1", "missing")) is False


def test_os_failure_during_write_raises_storage_error_and_leaves_no_temp_file(
    tmp_path: Path, monkeypatch
):
    storage = LocalFileStorage(tmp_path)
    key = certificate_key("job-1", "cert-1")

    import os as os_module

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os_module, "replace", boom)

    with pytest.raises(StorageError):
        storage.save(key, b"data")

    job_dir = tmp_path / "certificates" / "job-1"
    assert not job_dir.exists() or list(job_dir.glob(".tmp-*")) == []
