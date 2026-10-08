"""Liveness endpoint. Checks database connectivity with a trivial `SELECT 1`."""

import logging

import sqlalchemy as sa
from fastapi import APIRouter, Request, Response

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
def health(request: Request, response: Response) -> dict:
    """Report liveness plus a DB connectivity check.

    This body is the spec'd health payload, not the `{"error": {...}}` envelope used elsewhere
    (API_SPEC.md "GET /health") — health checks have their own, simpler contract.
    """
    session_factory = request.app.state.session_factory
    session = session_factory()
    try:
        session.execute(sa.text("SELECT 1"))
    except Exception:
        logger.exception("Health check database query failed")
        response.status_code = 503
        return {"status": "degraded", "database": "error"}
    finally:
        session.close()

    return {"status": "ok", "database": "ok"}
