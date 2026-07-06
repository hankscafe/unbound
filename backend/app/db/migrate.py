"""Run Alembic migrations at startup, adopting pre-Alembic databases safely.

- Fresh DB (no tables): upgrade to head creates the schema.
- Existing DB created by the old ``create_all`` path (tables, but no
  ``alembic_version``): stamp it at head so future migrations apply cleanly
  without trying to recreate existing tables.
- Already-migrated DB: upgrade to head.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

from app.core.logging import get_logger
from app.db.session import engine

log = get_logger("db.migrate")


def _config() -> Config:
    backend_dir = Path(__file__).resolve().parents[2]  # .../backend  (/app in Docker)
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    return cfg


def run_migrations() -> None:
    cfg = _config()
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    has_tables = "users" in inspect(engine).get_table_names()

    if current is None and has_tables:
        log.info("adopting_pre_alembic_db", action="stamp head")
        command.stamp(cfg, "head")
    else:
        command.upgrade(cfg, "head")
        log.info("migrations_applied")
