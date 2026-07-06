"""Unbound FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routers import accounts, auth, jobs, library, settings as settings_router
from app.api.routers import stats, stream
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import create_db_and_tables

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    configure_logging(
        level="DEBUG" if settings.debug else "INFO",
        json_logs=settings.environment == "production",
        log_dir=settings.log_dir,
    )
    log = get_logger("main")
    try:
        from app.db.migrate import run_migrations

        run_migrations()
    except Exception as exc:  # keep the app bootable even if migrations fail
        log.error("migration_failed_fallback_create_all", error=str(exc))
        create_db_and_tables()
    # Seed key fingerprint if an admin already exists (post-setup boots).
    from sqlmodel import Session

    from app.db import init_db
    from app.db.session import engine

    with Session(engine) as session:
        if init_db.admin_exists(session):
            init_db.seed_defaults(session)
            if init_db.secret_key_rotated(session):
                log.warning("secret_key_rotated",
                            msg="Master key changed; stored secrets may be undecryptable")

    from app.services.scheduler import start_scheduler, stop_scheduler
    from app.worker.queue import start_worker, shutdown_worker

    start_worker()
    start_scheduler()
    log.info("unbound_started", version=__version__, environment=settings.environment)
    try:
        yield
    finally:
        stop_scheduler()
        shutdown_worker()


app = FastAPI(
    title="Unbound API",
    version=__version__,
    description="Self-hosted Audible library manager — connect, decrypt, organize.",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def security_headers(request, call_next):
    """Defense-in-depth response headers (also set at the nginx edge)."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    # HSTS only makes sense over TLS; gated on the secure-cookie flag as a proxy.
    if settings.cookie_secure:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


api = "/api"
app.include_router(auth.router, prefix=f"{api}/auth")
app.include_router(accounts.router, prefix=f"{api}/accounts")
app.include_router(library.router, prefix=f"{api}/library")
app.include_router(jobs.router, prefix=f"{api}/jobs")
app.include_router(settings_router.router, prefix=f"{api}/settings")
app.include_router(stats.router, prefix=api)
app.include_router(stream.router, prefix=api)
