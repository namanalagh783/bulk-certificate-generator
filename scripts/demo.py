"""End-to-end demo client for the Bulk Certificate Generator API.

Submits a sample request, polls the job until it reaches a terminal state, prints per-certificate
results, and downloads the ZIP of succeeded PDFs. This is a demo/ops script (not part of the
`app` package), so plain `print` and a direct `httpx` dependency are fine here (CLAUDE.md allows
`print` in scripts/; httpx is already a dev dependency used for tests).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

TERMINAL_JOB_STATUSES = {"completed", "completed_with_errors", "failed"}
POLL_INTERVAL_SECONDS = 0.5


def parse_args(argv: list[str]) -> argparse.Namespace:
    """CLI flags. Defaults match the brief so `python scripts/demo.py` just works."""
    parser = argparse.ArgumentParser(description="Submit a sample job and download its results.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--file", default="samples/sample_request_with_errors.json")
    parser.add_argument("--out-dir", default="./data")
    parser.add_argument("--timeout", type=float, default=60.0)
    return parser.parse_args(argv)


def submit_job(client: httpx.Client, payload: dict[str, Any]) -> dict[str, Any]:
    """POST the request body and print the acceptance snapshot (summary + invalid rows)."""
    response = client.post("/api/v1/certificate-jobs", json=payload)
    response.raise_for_status()
    body = response.json()

    print(f"job_id: {body['job_id']}")
    print(f"summary: {body['summary']}")
    for row in body["invalid_recipients"]:
        print(f"  invalid row[{row['index']}] ({row['code']}): {row['errors']}")
    return body


def poll_job(client: httpx.Client, job_id: str, timeout: float) -> dict[str, Any]:
    """Poll GET job every 0.5s, printing progress, until terminal or `timeout` elapses.

    Polling (rather than a websocket/callback) matches the DB-is-source-of-truth design: the
    client just re-reads the same state the API exposes (ARCHITECTURE §7.3).
    """
    deadline = time.monotonic() + timeout
    while True:
        response = client.get(f"/api/v1/certificate-jobs/{job_id}")
        response.raise_for_status()
        job = response.json()
        progress = job["progress"]
        print(
            f"  status={job['status']} "
            f"processed={progress['processed']}/{progress['processable']} "
            f"({progress['percent_complete']}%)"
        )
        if job["status"] in TERMINAL_JOB_STATUSES:
            return job
        if time.monotonic() >= deadline:
            raise TimeoutError(f"job {job_id} did not finish within {timeout}s")
        time.sleep(POLL_INTERVAL_SECONDS)


def print_certificates(client: httpx.Client, job_id: str) -> None:
    """Print every certificate's status, paging through `.../certificates` as needed."""
    offset = 0
    limit = 50
    while True:
        response = client.get(
            f"/api/v1/certificate-jobs/{job_id}/certificates",
            params={"limit": limit, "offset": offset},
        )
        response.raise_for_status()
        body = response.json()
        for item in body["items"]:
            name = item["recipient"]["name"]
            print(f"  [{item['index']}] {name!r} -> {item['status']}")
        offset += len(body["items"])
        if offset >= body["pagination"]["total"] or not body["items"]:
            return


def download_zip(client: httpx.Client, job_id: str, out_dir: Path) -> Path | None:
    """Download the job's ZIP to `<out_dir>/certificates_<job_id>.zip`.

    Returns None (and prints a note) when the job produced zero succeeded certificates, since the
    API returns 409 NO_CERTIFICATES_AVAILABLE in that case rather than an empty archive.
    """
    response = client.get(f"/api/v1/certificate-jobs/{job_id}/download")
    no_certs = response.status_code == 409 and response.json()["error"]["code"] == (
        "NO_CERTIFICATES_AVAILABLE"
    )
    if no_certs:
        print("No certificates available to download (all recipients invalid or failed).")
        return None
    response.raise_for_status()

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"certificates_{job_id}.zip"
    zip_path.write_bytes(response.content)
    print(f"downloaded: {zip_path}")
    return zip_path


def run(args: argparse.Namespace) -> int:
    """Submit -> poll -> list -> download. Returns a process exit code."""
    payload = json.loads(Path(args.file).read_text(encoding="utf-8"))

    with httpx.Client(base_url=args.base_url, timeout=args.timeout) as client:
        acceptance = submit_job(client, payload)
        job_id = acceptance["job_id"]

        job = poll_job(client, job_id, args.timeout)
        print(f"final status: {job['status']}")

        print_certificates(client, job_id)
        download_zip(client, job_id, Path(args.out_dir))

    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return run(args)
    except httpx.HTTPStatusError as exc:
        print(f"HTTP error: {exc.response.status_code} {exc.response.text}", file=sys.stderr)
        return 1
    except TimeoutError as exc:
        print(f"Timeout: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
