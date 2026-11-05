"""Problem-details responses with stable error codes (RFC 9457, ADR-0008).

Every error the API returns is ``application/problem+json`` with a stable
``code`` clients can branch on:

    {"type": ".../problems/<code>", "title": ..., "status": ...,
     "code": "<code>", "detail": ...}

Two sources map onto it: :class:`ProblemError` for API-contract violations
(missing headers, malformed values) and :class:`~novel_llm.domain.errors.DomainError`
for domain rule violations.
"""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from novel_llm.domain.errors import DomainError

PROBLEM_MEDIA_TYPE = "application/problem+json"


class Problem(BaseModel):
    """RFC 9457 problem-details body with a stable ``code`` (ADR-0008).

    Declared in route ``responses=`` so the generated typed client models the
    error surface instead of discovering it at runtime."""

    type: str
    title: str
    status: int
    code: str
    detail: str | None = None


class ProblemError(Exception):
    """An API-contract violation carrying its problem-details presentation."""

    def __init__(self, *, status: int, code: str, title: str, detail: str = "") -> None:
        super().__init__(detail or title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail


def problem_response(
    *, status: int, code: str, title: str, detail: str = "", headers: dict[str, str] | None = None
) -> JSONResponse:
    body: dict[str, object] = {
        "type": f"https://novel-llm.dev/problems/{code}",
        "title": title,
        "status": status,
        "code": code,
    }
    if detail:
        body["detail"] = detail
    return JSONResponse(body, status_code=status, media_type=PROBLEM_MEDIA_TYPE, headers=headers)


def _domain_error_response(error: DomainError) -> JSONResponse:
    return problem_response(
        status=error.status,
        code=error.code,
        title=error.title,
        detail=error.detail,
        headers=error.headers,
    )


def _problem_error_response(error: ProblemError) -> JSONResponse:
    return problem_response(
        status=error.status, code=error.code, title=error.title, detail=error.detail
    )


def _validation_error_response(error: RequestValidationError) -> JSONResponse:
    summary = "; ".join(
        f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in error.errors()[:5]
    )
    return problem_response(
        status=400,
        code="validation_error",
        title="Request validation failed",
        detail=summary,
    )


Handler = Callable[[Request, Exception], Awaitable[JSONResponse]]


def install_problem_handlers(app: FastAPI) -> None:
    # Starlette's handler registry types the error parameter as Exception;
    # the registered class guarantees the narrower runtime type.
    async def on_domain_error(_: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, DomainError)
        return _domain_error_response(error)

    async def on_problem_error(_: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, ProblemError)
        return _problem_error_response(error)

    async def on_validation_error(_: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, RequestValidationError)
        return _validation_error_response(error)

    app.add_exception_handler(DomainError, on_domain_error)
    app.add_exception_handler(ProblemError, on_problem_error)
    app.add_exception_handler(RequestValidationError, on_validation_error)
