"""Top-level error middleware.

Catches every exception that escapes a route handler, maps the known
:class:`z4j_core.errors.Z4JError` subclasses to HTTP status codes via
:func:`z4j_brain.errors.http_status_for`, and renders a stable JSON
shape. Pydantic ``ValidationError`` is mapped to 422 with a redacted
summary that does NOT echo the offending input value.

In production this middleware NEVER includes a stack trace in the
response body. The full traceback is logged with the request id so
operators can correlate.
"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import ValidationError as PydanticValidationError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from z4j_brain.errors import Z4JError, http_status_for

logger = structlog.get_logger("z4j.brain.errors")


class ErrorMiddleware(BaseHTTPMiddleware):
    """Catch unhandled exceptions and return a structured JSON response."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        try:
            return await call_next(request)
        except Z4JError as exc:
            return _z4j_error_response(request, exc)
        except PydanticValidationError as exc:
            return _pydantic_validation_response(request, exc)
        except Exception as exc:  # noqa: BLE001
            return _unexpected_error_response(request, exc)


def _z4j_error_response(request: Request, exc: Z4JError) -> JSONResponse:
    status = http_status_for(exc)
    request_id = getattr(request.state, "request_id", None)

    logger.warning(
        "z4j request failed",
        error_code=exc.code,
        error_class=type(exc).__name__,
        status=int(status),
        path=request.url.path,
    )

    body: dict[str, Any] = {
        "error": exc.code,
        "message": exc.message,
        "request_id": request_id,
        "details": _safe_details(exc.details),
    }
    return JSONResponse(content=body, status_code=int(status))


def _pydantic_validation_response(
    request: Request,
    exc: PydanticValidationError,
) -> JSONResponse:
    """Map a Pydantic ValidationError to a redacted 422.

    The summary lists each error's location and type but NOT its
    input value - the input may contain caller-supplied secrets and
    we don't want them in our error responses or our log lines.
    """
    request_id = getattr(request.state, "request_id", None)
    summary = [
        {
            "loc": ".".join(str(p) for p in err["loc"]),
            "type": err["type"],
        }
        for err in exc.errors()
    ]
    logger.info(
        "z4j request validation failed",
        error_count=len(summary),
        path=request.url.path,
    )
    return JSONResponse(
        status_code=422,
        content={
            "error": "validation_failed",
            "message": f"request body failed validation ({len(summary)} field(s))",
            "request_id": request_id,
            "details": {"errors": summary},
        },
    )


def _unexpected_error_response(
    request: Request,
    exc: BaseException,
) -> JSONResponse:
    """Map any unhandled exception to a generic 500.

    The exception type is logged with the full traceback for the
    operator. The HTTP response intentionally exposes nothing about
    the cause beyond a request id - even the exception class name
    is withheld, since some libraries put secrets in their class
    names (RuntimeError("missing token sk_live_...")).
    """
    request_id = getattr(request.state, "request_id", None)
    logger.error(
        "z4j unhandled exception",
        error_class=type(exc).__name__,
        path=request.url.path,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": "the brain encountered an unexpected error",
            "request_id": request_id,
            "details": {},
        },
    )


def _safe_details(details: Any) -> dict[str, Any]:
    """Coerce ``Z4JError.details`` to a JSON-safe dict.

    The dict is shallow-copied so the caller's mutable details
    object cannot be observed by a malicious response interceptor.
    """
    if not isinstance(details, dict):
        return {}
    return {str(k): v for k, v in details.items()}


__all__ = ["ErrorMiddleware"]
