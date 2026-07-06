"""Database engine and session management."""

from __future__ import annotations

from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from app.core.config import get_settings

_settings = get_settings()

_connect_args = {}
if _settings.database_url.startswith("sqlite"):
    # Allow use across threads (FastAPI/uvicorn workers) for SQLite.
    _connect_args = {"check_same_thread": False}

engine = create_engine(
    _settings.database_url,
    echo=False,
    connect_args=_connect_args,
    pool_pre_ping=True,
)


def create_db_and_tables() -> None:
    """Create tables from SQLModel metadata (used for first-run / SQLite).

    Production migrations are managed by Alembic; this is a convenience for
    fresh installs and tests so the app is runnable without a migration step.
    """
    # Import models so they register on SQLModel.metadata.
    from app.db import models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
