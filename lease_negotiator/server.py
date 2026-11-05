"""Production FastAPI server wrapping Google ADK with persistent DB state, OTel, and health probes."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
import os
from pathlib import Path
import time
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from google.adk.cli.fast_api import get_fast_api_app
import uvicorn

from . import __version__
from .config import SETTINGS
from .memory.vector_store import get_default_vector_store
from .observability.logging_config import (
    bind_correlation_context,
    configure_logging,
    get_logger,
)
from .observability.telemetry import configure_telemetry, record_counter, record_histogram

logger = get_logger("server")


def _ensure_sqlite_parent_dir(session_uri: str) -> None:
    """Create parent directory on disk when `SESSION_DB_URL` targets a local SQLite database."""
    prefixes = ("sqlite+aiosqlite:///", "sqlite:///")
    for prefix in prefixes:
        if session_uri.startswith(prefix):
            raw_path = session_uri[len(prefix) :]
            if raw_path and raw_path != ":memory:":
                Path(raw_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
            break


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Initialize logging, OpenTelemetry exporters, SQLite storage, and async vector store."""
    configure_logging(level=SETTINGS.log_level, log_format=SETTINGS.log_format)
    configure_telemetry(SETTINGS)
    _ensure_sqlite_parent_dir(SETTINGS.session_db_url)
    vector_store = get_default_vector_store(SETTINGS)
    await vector_store.initialize()
    logger.info(
        "LeaseNegotiator FastAPI server ready",
        extra={
            "version": __version__,
            "vector_backend": vector_store.backend_name,
            "deployment_env": SETTINGS.deployment_env,
        },
    )
    yield
    logger.info("LeaseNegotiator FastAPI server shutting down cleanly")


def build_fastapi_app() -> FastAPI:
    """Construct the production FastAPI ASGI application hosting ADK API + optional Web UI."""
    agents_root = str(Path(__file__).resolve().parent.parent)
    enable_web_ui = os.getenv("SERVE_ADK_WEB_UI", "true").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    raw_origins = os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")
    allow_origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]

    app = get_fast_api_app(
        agents_dir=agents_root,
        session_service_uri=SETTINGS.session_db_url,
        allow_origins=allow_origins,
        web=enable_web_ui,
        otel_to_cloud=os.getenv("OTEL_TO_CLOUD", "false").lower() == "true",
        lifespan=_lifespan,
    )

    @app.middleware("http")
    async def correlation_and_metrics_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        req_id = request.headers.get("x-correlation-id") or f"req_{uuid.uuid4().hex[:12]}"
        bind_correlation_context(correlation_id=req_id)
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        response.headers["X-Correlation-ID"] = req_id
        record_counter(
            "http_requests",
            1,
            {"method": request.method, "path": request.url.path, "status": response.status_code},
        )
        record_histogram(
            "http_duration_ms",
            elapsed_ms,
            {"method": request.method, "path": request.url.path},
        )
        return response

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        """Lightweight container liveness probe."""
        return {
            "status": "ok",
            "service": SETTINGS.otel_service_name,
            "version": __version__,
        }

    @app.get("/readyz", tags=["ops"])
    async def readyz() -> JSONResponse:
        """Deep readiness probe checking persistent vector memory initialization and model config."""
        try:
            store = get_default_vector_store(SETTINGS)
            await store.initialize()
            return JSONResponse(
                status_code=200,
                content={
                    "status": "ready",
                    "version": __version__,
                    "vector_backend": store.backend_name,
                    "models": {
                        "pro": SETTINGS.pro_model,
                        "flash": SETTINGS.flash_model,
                        "lite": SETTINGS.lite_model,
                    },
                    "api_key_configured": bool(os.getenv("GOOGLE_API_KEY")),
                },
            )
        except Exception as exc:  # pragma: no cover
            logger.error("Readiness probe failed: %s", exc)
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": type(exc).__name__},
            )

    return app


fastapi_app = build_fastapi_app()


def main() -> None:
    """CLI entrypoint (`lease-negotiator-server`) launching Uvicorn."""
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8080"))
    workers = int(os.getenv("WEB_CONCURRENCY", "1"))
    uvicorn.run(
        "lease_negotiator.server:fastapi_app",
        host=host,
        port=port,
        workers=workers,
        log_config=None,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
