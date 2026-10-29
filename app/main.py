"""App factory: wires the pool, routes, request ids and the error handlers.

All errors, ours or the framework's, leave through `error_response` so clients always see
{"error": {"code", "message", "details", "request_id"}}.
"""

import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from psycopg import errors as pg_errors
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import database_url
from app.db import create_pool
from app.errors import AppError
from app.routes import router

logger = logging.getLogger("ledger")

# Which validation failures get a specific error code instead of the generic VALIDATION_ERROR.
FIELD_ERROR_CODES = {
    "type": "INVALID_TRANSACTION_TYPE",
    "amount": "INVALID_AMOUNT",
    "page": "INVALID_PAGINATION",
    "limit": "INVALID_PAGINATION",
}


def error_response(request: Request, status_code: int, code: str, message: str, details=None) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details, "request_id": request_id}},
        headers={"X-Request-ID": request_id} if request_id else None,
    )


def classify_validation_errors(errors: list[dict]) -> tuple[str, list[dict]]:
    # Body missing, not JSON, or not a JSON object: the error is on the body as a whole.
    if any(e["loc"] == ("body",) or e["type"] == "json_invalid" for e in errors):
        return "INVALID_REQUEST_BODY", [{"field": "body", "message": "Request body must be a valid JSON object"}]
    # loc is like ("body", "amount") or ("query", "page"); report the field name without the prefix.
    details = [
        {"field": ".".join(str(p) for p in e["loc"][1:]) or e["loc"][0], "message": e["msg"]}
        for e in errors
    ]
    codes = {FIELD_ERROR_CODES.get(str(e["loc"][-1]), "VALIDATION_ERROR") for e in errors}
    # A specific code only when every problem is of that one kind; mixed problems -> generic.
    return (codes.pop() if len(codes) == 1 else "VALIDATION_ERROR"), details


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        return error_response(request, exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        code, details = classify_validation_errors(exc.errors())
        first = details[0]
        return error_response(request, 400, code, f"Invalid request: {first['field']}: {first['message']}", details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return error_response(request, exc.status_code, code, str(exc.detail))

    # Backstops: the service layer prevents these, but if a future bug slips past it,
    # the database constraint still says no and the client still gets the right answer.
    @app.exception_handler(pg_errors.UniqueViolation)
    async def unique_violation(request: Request, exc: pg_errors.UniqueViolation):
        logger.warning("unique constraint backstop hit: %s", exc.diag.constraint_name)
        return error_response(request, 409, "DUPLICATE_TRANSACTION", "Transaction already exists")

    @app.exception_handler(pg_errors.CheckViolation)
    async def check_violation(request: Request, exc: pg_errors.CheckViolation):
        logger.warning("check constraint backstop hit: %s", exc.diag.constraint_name)
        if exc.diag.constraint_name == "accounts_balance_non_negative":
            return error_response(request, 422, "INSUFFICIENT_BALANCE", "Insufficient balance")
        return error_response(request, 400, "VALIDATION_ERROR", "Request violates a data constraint")

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        logger.exception("unhandled error")
        return error_response(request, 500, "INTERNAL_ERROR", "An unexpected error occurred; nothing was changed")


def create_app(db_url: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.pool = create_pool(db_url or database_url())
        app.state.pool.open(wait=True)
        yield
        app.state.pool.close()

    app = FastAPI(
        title="Transaction Ledger API",
        version="1.0.0",
        description="Accounts, idempotent credit/debit transactions, paginated history and atomic transfers.",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    register_error_handlers(app)
    app.include_router(router)
    app.openapi = lambda: openapi_schema(app)
    return app


def openapi_schema(app: FastAPI) -> dict:
    """FastAPI documents request-validation failures as 422 HTTPValidationError on every route,
    but this API returns them as 400 ErrorResponse. Correct the generated spec to match."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
    error_ref = {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}}
    for path in schema["paths"].values():
        for operation in path.values():
            responses = operation["responses"]
            if "HTTPValidationError" in str(responses.get("422", "")):
                del responses["422"]
                responses.setdefault("400", {"description": "Validation error", **error_ref})
    for name in ("HTTPValidationError", "ValidationError"):
        schema["components"]["schemas"].pop(name, None)
    app.openapi_schema = schema
    return schema


app = create_app()
