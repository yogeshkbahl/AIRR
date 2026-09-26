"""Application entrypoint."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api.v1.routes import router as v1_router
from .config import settings
from .core.store import store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("ai_bi_analyst")

REDACT = ("api_key", "apikey", "authorization", "x-api-key", "secret", "token", "password")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    purged = store.purge_expired()
    if purged:
        log.info(
            "Purged %s expired session(s) beyond the %sh retention window", purged, settings.session_retention_hours
        )
    yield


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "A governed analytical assistant. Python computes every number; the language model only "
        "interprets computed results and proposes reporting ideas."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    request_id = getattr(request.state, "request_id", "unknown")
    if isinstance(exc.detail, dict):
        payload = {**exc.detail, "request_id": exc.detail.get("request_id", request_id)}
    else:
        payload = {"error": "request_failed", "detail": str(exc.detail), "request_id": request_id}
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    request_id = getattr(request.state, "request_id", "unknown")
    fields = []
    for err in exc.errors():
        location = ".".join(str(p) for p in err.get("loc", []) if p not in ("body", "query"))
        fields.append(f"{location or 'request'}: {err.get('msg')}")
    return JSONResponse(
        status_code=422,
        content={"error": "validation_failed", "detail": "; ".join(fields[:5]), "request_id": request_id},
    )


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", "unknown")
    log.exception("Unhandled error on %s (request_id=%s)", request.url.path, request_id)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "detail": f"The request failed ({type(exc).__name__}). Quote request id {request_id} when reporting it.",
            "request_id": request_id,
        },
    )


app.include_router(v1_router, prefix=settings.api_prefix, tags=["v1"])


@app.get("/health")
def root_health() -> dict[str, str]:
    return {"status": "ok", "api": settings.api_prefix}
