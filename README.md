# Bulk Certificate Generator API

A FastAPI backend that accepts a list of recipients plus certificate details in a single
request, validates each recipient individually, generates a PDF certificate for every valid
recipient from one predefined template in the background, tracks per-job and per-certificate
status, and serves the results individually or as a ZIP.

This is an interview take-home: optimised for correctness, clarity and small, explainable units.

## Tech stack
Python 3.11+, FastAPI, Uvicorn, Pydantic v2, SQLAlchemy 2.0 (sync) + Alembic, SQLite (default) /
PostgreSQL, ReportLab, pytest.

## Table of contents
0. [Quick start with Docker (recommended)](#0-quick-start-with-docker-recommended)
1. [Setup (local Python)](#1-setup-local-python)
2. [Run](#2-run)
3. [Run tests](#3-run-tests)
4. [Submit a certificate generation request](#4-submit-a-certificate-generation-request)
5. [Check status / progress](#5-check-status--progress)
6. [Retrieve generated certificates](#6-retrieve-generated-certificates)
7. [API endpoints](#7-api-endpoints)
8. [Error envelope](#8-error-envelope)
9. [Configuration](#9-configuration)
10. [Design decisions](#10-design-decisions-summary)
11. [Project layout](#11-project-layout)
12. [Docker / PostgreSQL](#12-docker--postgresql)
13. [Known limitations](#13-known-limitations)
14. [Scaling path](#14-scaling-path)

## 0. Quick start with Docker (recommended)
Prerequisites: **Docker Desktop only** — no Python, no local Postgres install.

```bash
docker compose up --build
```

This builds the image, starts Postgres 16, runs migrations, and **seeds two demo jobs**
(`scripts/seed.py`, ADR-020) from `samples/sample_request.json` (5 valid recipients, job ends
`completed`) and `samples/sample_request_with_errors.json` (mixed valid/invalid rows, job ends
`completed_with_errors`) — through the real `create_job` + `JobProcessor` code paths, so their
PDFs already exist on disk by the time the API starts serving traffic.

Find the seeded job ids and their URLs in the API container's logs:
```bash
docker compose logs api
```
Then open the interactive docs at http://localhost:8000/docs, or call the endpoints directly,
e.g. (substitute a job id from the logs):
```bash
curl -s http://localhost:8000/api/v1/certificate-jobs/<job_id>
curl -s -OJ http://localhost:8000/api/v1/certificate-jobs/<job_id>/download   # ZIP of PDFs
```

Stop the stack (keeps data for next time):
```bash
docker compose down
```
Reset everything, including the seeded demo data (next `up` reseeds from scratch):
```bash
docker compose down -v
```

Seeding is idempotent — restarting the `api` container (or running `docker compose up` again)
leaves the existing jobs untouched; `scripts/seed.py` only acts on an empty database, logging
`Database already contains jobs — skipping seed.` otherwise.

To point a local (non-Docker) `uvicorn` or `scripts/demo.py` at this same containerised
Postgres (e.g. while iterating on code without rebuilding the image), set:
```bash
DATABASE_URL=postgresql+psycopg://certgen:certgen@localhost:5433/certgen uvicorn app.main:app --reload
```
(Postgres is published on host port **5433**, not 5432, so it never clashes with a host Postgres
install.) `python scripts/seed.py` also works standalone against any `DATABASE_URL` the local
`Settings` resolve to — useful for seeding a local SQLite dev database the same way.

The rest of this README (sections 1–3) covers the local Python/SQLite path, which is also how
the test suite runs (`pytest -q` always uses SQLite regardless of `DATABASE_URL`, per the test
fixtures in `tests/conftest.py`).

## 1. Setup (local Python)
```bash
python -m venv .venv

# macOS/Linux
source .venv/bin/activate
# Windows (Git Bash)
source .venv/Scripts/activate
# Windows (cmd.exe / PowerShell)
.venv\Scripts\activate

pip install -e ".[dev]"
cp .env.example .env          # optional; every setting has a working default
alembic upgrade head
```
Optional: drop `DejaVuSans.ttf` and `DejaVuSans-Bold.ttf` into `assets/fonts/` for full Unicode
name rendering (see [Known limitations](#13-known-limitations)).

## 2. Run
```bash
uvicorn app.main:app --reload
```
Interactive API docs: http://127.0.0.1:8000/docs
Health check: http://127.0.0.1:8000/health

Run with exactly **one** Uvicorn process — background work runs as threads inside that same
process (see [ADR-003](docs/DESIGN_DECISIONS.md)).
`uvicorn --workers N` or multiple processes would each run their own thread pool and their own
startup recovery and is not supported.

## 3. Run tests
```bash
pytest -q
pytest --cov=app -q         # with coverage (≈99% at the time of writing)
```

## 4. Submit a certificate generation request
Using the bundled sample (5 valid recipients):
```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/certificate-jobs \
  -H "Content-Type: application/json" \
  -d @samples/sample_request.json
```
This returns **202 Accepted** immediately with a `job_id`, a `Location` header, and an
acceptance snapshot. A second sample, `samples/sample_request_with_errors.json`, mixes valid
recipients with an empty name, an unknown extra field, a non-object item and a case-insensitive
duplicate email — useful for seeing partial acceptance in action:
```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/certificate-jobs \
  -H "Content-Type: application/json" \
  -d @samples/sample_request_with_errors.json
```
Invalid recipients are reported in `invalid_recipients` and never generated; valid ones proceed
in the background.

Or run the end-to-end demo script, which submits, polls, prints progress, and downloads the ZIP
for you:
```bash
python scripts/demo.py
```

## 5. Check status / progress
```bash
curl -s http://127.0.0.1:8000/api/v1/certificate-jobs/<job_id>
curl -s "http://127.0.0.1:8000/api/v1/certificate-jobs/<job_id>/certificates?status=failed"
```
The job response includes a `progress` block (`total`, `invalid`, `pending`, `processing`,
`succeeded`, `failed`, `processable`, `processed`, `percent_complete`) and a final `status` of
`completed`, `completed_with_errors`, or `failed` once processing ends.

## 6. Retrieve generated certificates
```bash
# One certificate — saves using the server's suggested filename (-OJ honours Content-Disposition)
curl -s -OJ http://127.0.0.1:8000/api/v1/certificates/<certificate_id>/download
# ...or save it under a name you choose
curl -s -o cert.pdf http://127.0.0.1:8000/api/v1/certificates/<certificate_id>/download

# Everything for a job (ZIP with all succeeded PDFs + manifest.csv), once the job is terminal
curl -s -OJ http://127.0.0.1:8000/api/v1/certificate-jobs/<job_id>/download
curl -s -o certs.zip http://127.0.0.1:8000/api/v1/certificate-jobs/<job_id>/download
```
`manifest.csv` inside the ZIP lists every submitted row (`row,name,email,status,
certificate_number,file,error_code,error_message`) so a non-technical user can see what
succeeded, failed, or was rejected at validation, without opening the API.

## 7. API endpoints
Base path `/api/v1`. Full contract: [docs/API_SPEC.md](docs/API_SPEC.md).

| Method | Path | Purpose | Success | Errors |
|---|---|---|---|---|
| POST | `/certificate-jobs` | Create a bulk job | 202 | 422 |
| GET | `/certificate-jobs/{job_id}` | Status + progress | 200 | 404 |
| GET | `/certificate-jobs/{job_id}/certificates` | Per-recipient results (paginated, filterable by `status`) | 200 | 404, 422 |
| GET | `/certificate-jobs/{job_id}/download` | ZIP of succeeded PDFs + manifest | 200 | 404, 409 |
| GET | `/certificates/{certificate_id}` | One certificate's result | 200 | 404 |
| GET | `/certificates/{certificate_id}/download` | One PDF | 200 | 404, 409 |
| GET | `/health` (no prefix) | Liveness + DB check | 200 | 503 |

## 8. Error envelope
Every error — domain errors, FastAPI request-validation errors, and framework HTTP errors
(unknown route, wrong method) — uses the same shape:
```json
{ "error": { "code": "JOB_NOT_FOUND", "message": "Job 3f6c... not found", "details": null } }
```

| HTTP | code | When |
|---|---|---|
| 422 | `VALIDATION_ERROR` | Malformed request envelope (missing/invalid certificate fields, etc.) |
| 422 | `TOO_MANY_RECIPIENTS` | More than `MAX_RECIPIENTS_PER_JOB` recipients submitted |
| 422 | `NO_VALID_RECIPIENTS` | Every recipient failed validation — no job is created |
| 404 | `JOB_NOT_FOUND` / `CERTIFICATE_NOT_FOUND` | Unknown id |
| 409 | `CERTIFICATE_NOT_READY` | Download requested before the certificate succeeded |
| 409 | `JOB_NOT_FINISHED` | ZIP requested before the job reached a terminal status |
| 409 | `NO_CERTIFICATES_AVAILABLE` | ZIP requested but zero certificates succeeded |
| 404 / 405 / 4xx-5xx | `NOT_FOUND` / `METHOD_NOT_ALLOWED` / `HTTP_ERROR` | Any other framework HTTP error, wrapped in the same envelope |
| 500 | `INTERNAL_ERROR` | Unexpected error (logged; no internals leaked) |

Per-recipient (certificate-level) error codes, visible in job/certificate responses and in
`manifest.csv`: `VALIDATION_ERROR`, `DUPLICATE_RECIPIENT`, `RENDER_ERROR`, `STORAGE_ERROR`,
`INTERNAL_ERROR`.

## 9. Configuration
All settings are read once via `app/config.py::Settings` (pydantic-settings), from environment
variables or a `.env` file. See `.env.example` for a ready-to-copy file.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/certgen.db` | SQLAlchemy URL. Use `postgresql+psycopg://...` for Postgres |
| `STORAGE_DIR` | `./data/storage` | Root directory for generated PDF files |
| `DISPATCHER` | `thread` | `thread` (real background pool) or `inline` (synchronous; used by tests) |
| `WORKER_CONCURRENCY` | `2` | Number of jobs the thread pool processes in parallel |
| `MAX_RECIPIENTS_PER_JOB` | `1000` | Envelope-level cap; exceeding it returns 422 `TOO_MANY_RECIPIENTS` |
| `RECOVER_ON_STARTUP` | `true` | Re-queue unfinished jobs found at process startup |
| `LOG_LEVEL` | `INFO` | Root logger level |

The SQLite file's parent directory and `STORAGE_DIR` are created automatically on startup and by
Alembic — no manual `mkdir` needed.

## 10. Design decisions (summary)
Full rationale, alternatives considered and consequences for every decision are in
[docs/DESIGN_DECISIONS.md](docs/DESIGN_DECISIONS.md) (ADR-001 to ADR-021). Highlights:

- **Background processing ([ADR-003](docs/DESIGN_DECISIONS.md)):** `POST` persists the job and
  returns 202 immediately; a `JobDispatcher` hands the job id to a worker. The default is an
  in-process bounded `ThreadPoolExecutor` (`ThreadPoolDispatcher`); tests use a synchronous
  `InlineDispatcher`. Rendering a few hundred PDFs inline would tie up the HTTP connection and
  risk client/proxy timeouts; FastAPI `BackgroundTasks` has no concurrency limit or recovery
  story; Celery/RQ + Redis is the right production answer but asks a reviewer to run extra
  infrastructure for a take-home. The database is the single source of truth, so unfinished jobs
  are recovered and re-queued on startup. Swapping to Celery/RQ later is a single-class change
  behind the same `JobDispatcher` protocol. Consequence: run exactly one Uvicorn process (§2).
- **Partial acceptance validation ([ADR-004](docs/DESIGN_DECISIONS.md)):** two levels — the
  request envelope (certificate fields, recipients shape) is validated strictly and rejects the
  whole request on error; each recipient is then validated individually, so one bad row never
  blocks the rest. If every recipient is invalid, nothing is persisted (422
  `NO_VALID_RECIPIENTS`). This matches the brief's "a failure... should not unnecessarily
  prevent other valid certificates... from being generated."
- **Status model ([ADR-005](docs/DESIGN_DECISIONS.md)):** job status is
  `pending → processing → {completed, completed_with_errors, failed}`; certificate status is
  `invalid` (terminal immediately) or `pending → processing → {succeeded, failed}`. Progress
  counts are computed on read with a single `GROUP BY status` query rather than stored counters,
  so they can never drift out of sync with the actual rows.
- **Failure isolation ([ADR-008](docs/DESIGN_DECISIONS.md)):** each certificate is claimed,
  rendered, stored and marked in its own try/except and its own small transaction. Any exception
  is classified (`RENDER_ERROR` / `STORAGE_ERROR` / `INTERNAL_ERROR`), recorded on that row, and
  the loop continues — one failure never stops the rest of the job.
- **Rendering ([ADR-006](docs/DESIGN_DECISIONS.md)):** ReportLab, a pure-Python library with no
  system dependencies (unlike e.g. WeasyPrint, which needs Cairo/Pango). One template
  (`classic_v1`) is defined as a layout spec in code; the renderer sits behind a
  `CertificateRenderer` protocol so the format could change without touching the processor.
- **Storage ([ADR-007](docs/DESIGN_DECISIONS.md)):** local disk behind a `CertificateStorage`
  protocol. Files live at `STORAGE_DIR/certificates/<job_id>/<certificate_id>.pdf`; writes go to
  a temp file then `os.replace` (atomic — no reader ever sees a partial file); keys are built
  only from server-generated UUIDs, never from user input, so there is no path-traversal surface.

## 11. Project layout
```
app/
  main.py            create_app() factory, lifespan wiring, app = create_app()
  config.py          Settings (env vars / .env)
  db.py              engine, session factory, SQLite pragmas
  enums.py           JobStatus, CertificateStatus, ErrorCode
  models.py          CertificateJob, Certificate (SQLAlchemy ORM)
  schemas.py         Pydantic request/response models
  errors.py          AppError hierarchy (code, http_status, message, details)
  api/               routes (jobs, certificates, health), deps, error handlers, presenters
  services/          validation.py, job_service.py, retrieval.py — no FastAPI imports
  rendering/         CLASSIC_V1 template spec + PdfCertificateRenderer (ReportLab)
  storage/           CertificateStorage protocol + LocalFileStorage (atomic writes)
  workers/           JobDispatcher (thread pool / inline), JobProcessor, startup recovery
alembic/             migrations (0001_initial)
assets/fonts/         optional DejaVuSans*.ttf for Unicode names
samples/             sample_request.json, sample_request_with_errors.json
scripts/demo.py      submit → poll → list → download, end to end
tests/               pytest suite (unit + API + end-to-end via TestClient)
docs/                ARCHITECTURE.md, API_SPEC.md, DESIGN_DECISIONS.md, INTERVIEW_PREP.md, ...
```
Layering rule: `api` holds no business logic; `services`, `workers`, `rendering` and `storage`
never import FastAPI. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full picture.

## 12. Docker / PostgreSQL
`Dockerfile` builds a single-process image (migrations run on container start, then
`scripts/seed.py`, then Uvicorn with no `--workers`). `docker-compose.yml` runs that image
against a `postgres:16` container — this is the primary, recommended way to run the project
(see [§0](#0-quick-start-with-docker-recommended); ADR-020):
```bash
docker compose up --build
```
Verified live in this environment: the stack starts, `GET /health` reports `ok`, the two seeded
jobs reach `completed` / `completed_with_errors`, a single certificate PDF and a job ZIP both
download correctly (`%PDF` header), restarting the `api` container skips reseeding (job count
stays at 2), and `scripts/demo.py` runs end to end against the containerised API.

SQLite remains the `Settings` default for local development without Docker and for the test
suite ([ADR-002](docs/DESIGN_DECISIONS.md)); Postgres via Docker Compose is for demonstrating
the production-shaped configuration and for a zero-install reviewer experience.

## 13. Known limitations
- **Single process:** exactly one Uvicorn process (ADR-003). Horizontal scaling requires moving
  to an external queue first (§14).
- **Latin-1 font fallback ([ADR-017](docs/DESIGN_DECISIONS.md)):** no DejaVu fonts are bundled,
  so names are drawn with built-in Helvetica (Latin-1 only). Non-Latin-1 characters may render as
  missing glyphs. Fix: drop `DejaVuSans.ttf` and `DejaVuSans-Bold.ttf` into `assets/fonts/` —
  `app/rendering/fonts.py` registers them automatically if present, no code change needed.
- **No authentication:** anyone who can reach the API can submit jobs and download any
  certificate by id. Out of scope per the assignment; a production deployment needs API keys or
  OAuth and per-organisation job ownership (ADR-014).
- **Local disk storage:** files live on the server's local filesystem, not an object store.
  Fine for a single instance; doesn't survive a container being recreated without a volume.
- **No automatic retries:** a failed certificate stays `failed`; render failures are treated as
  deterministic rather than transient (ADR-008). Reprocessing would require a new job today.
- **Dispatch-after-commit corner case:** the job row and its certificate rows are committed
  *before* `dispatcher.submit(job_id)` is called (so a worker never sees rows that aren't there
  yet). If `submit` itself raises after that commit, the client gets a 500 even though the job
  now exists in the database as `pending`. It will not be lost: startup recovery picks up any
  `pending`/`processing` job the next time the process starts. This is a documented consequence
  of "commit, then dispatch," not a race in the processing logic itself.
- **Windows long paths:** files are stored at
  `STORAGE_DIR/certificates/<job_uuid>/<certificate_uuid>.pdf` (~90 chars of UUIDs, plus a
  temp-file name during the atomic write). If the project/`STORAGE_DIR` sits deep (e.g. under
  `AppData\Local\Temp`), the full path can exceed Windows' 260-char `MAX_PATH` and every
  certificate fails with `STORAGE_ERROR` (failure isolation still works; the job ends `failed`).
  Mitigation: keep the project/`STORAGE_DIR` path short, or enable Windows long-path support.

## 14. Scaling path
Documented, not built — see [docs/ARCHITECTURE.md §12](docs/ARCHITECTURE.md) for detail:
1. Swap `ThreadPoolDispatcher` for an external queue (Celery/RQ/Arq + Redis, or SQS) behind the
   same `JobDispatcher` protocol.
2. Separate worker processes from API processes; run multiple API processes behind a load
   balancer (now safe, since the queue — not in-process state — owns dispatch).
3. Swap `LocalFileStorage` for an S3-compatible `CertificateStorage` implementation, serving
   presigned download URLs instead of streaming through the API.
4. Batch commits per certificate instead of one commit each; parallelise rendering across
   worker processes.
5. Add auth, per-organisation job ownership, rate limits, and retention/cleanup of old files.

## Project docs
[docs/ASSIGNMENT.md](docs/ASSIGNMENT.md) ·
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) ·
[docs/API_SPEC.md](docs/API_SPEC.md) ·
[docs/DESIGN_DECISIONS.md](docs/DESIGN_DECISIONS.md) ·
[docs/TESTING_STRATEGY.md](docs/TESTING_STRATEGY.md) ·
[docs/INTERVIEW_PREP.md](docs/INTERVIEW_PREP.md) ·
[docs/PROGRESS.md](docs/PROGRESS.md)
