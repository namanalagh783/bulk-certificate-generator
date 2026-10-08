# Single-process image: FastAPI/Uvicorn serves the API, migrations run on container start
# (ARCHITECTURE §8 — exactly one Uvicorn process, no --workers). scripts/ and samples/ are
# included because docker-compose.yml's command runs scripts/seed.py (ADR-020), which reads
# samples/*.json.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY assets ./assets
COPY scripts ./scripts
COPY samples ./samples

# Editable install: app/rendering/fonts.py derives DEFAULT_FONT_DIR from its own file location
# (three parents up) to find assets/fonts next to the repo root. A normal `pip install .` copies
# the package into site-packages, where that walk would land inside site-packages instead of
# /app/assets. Installing editable keeps app/ at /app, so the three-parents-up walk still
# resolves to /app/assets — the simplest fix that preserves the TTF-lookup/Helvetica-fallback
# behaviour without touching app/ code.
RUN pip install --no-cache-dir -e .

# Non-root user owning the data directory (SQLite file + generated PDFs live under /app/data).
RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
