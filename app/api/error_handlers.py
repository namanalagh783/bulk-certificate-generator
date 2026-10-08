"""Uniform error envelope for every error response (ADR-019, CLAUDE.md).

Four cases, in order of specificity: our own `AppError`s, FastAPI's request-validation errors,
Starlette's generic HTTP exceptions (unknown route, wrong method, ...), and anything unhandled.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.errors import AppError

logger = logging.getLogger(__name__)

# Starlette HTTP exceptions carry only a status code; map the two we actually surface and fall
# back to a generic code for anything else (ADR-019).
_STATUS_TO_CODE = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}


def _envelope(code: str, message: str, details: object = None) -> dict:
    """Build the `{"error": {...}}` body (API_SPEC.md)."""
    return {"error": {"code": code, "message": message, "details": details}}


def register_error_handlers(app: FastAPI) -> None:
    """Attach the four handlers that make every error response use the same envelope."""

    @app.exception_handler(AppError)
    def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=_envelope(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic's error `ctx` can hold the original exception object, which isn't JSON
        # serialisable, so details are built from only `loc`/`msg` (API_SPEC.md).
        details = [{"loc": list(err["loc"]), "message": err["msg"]} for err in exc.errors()]
        return JSONResponse(
            status_code=422,
            content=_envelope("VALIDATION_ERROR", "Request validation failed", details),
        )

    @app.exception_handler(StarletteHTTPException)
    def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_TO_CODE.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(code, str(exc.detail)),
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    def handle_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        # Generic message only: the client never sees tracebacks, paths, or SQL (CLAUDE.md).
        logger.exception("Unhandled exception handling %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=_envelope("INTERNAL_ERROR", "An unexpected error occurred"),
        )
