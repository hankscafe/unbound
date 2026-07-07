"""Application configuration, loaded from environment / .env.

All settings are overridable via environment variables prefixed with ``UNBOUND_``
(e.g. ``UNBOUND_SECRET_KEY``). See ``deploy/.env.example`` for the full list.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="UNBOUND_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- General ---
    app_name: str = "Unbound"
    environment: str = "production"  # production | development
    debug: bool = False

    # --- Paths (defaults suit both Docker volumes and bare-metal) ---
    data_dir: Path = Path("./data")
    downloads_dir: Path = Path("./data/downloads")
    library_dir: Path = Path("./data/library")
    log_dir: Path = Path("./data/logs")
    # Built SPA directory; when it exists the API process serves the web UI too
    # (single-image deployment). Absent in dev/tests — the API runs alone.
    static_dir: Path = Path("./static")

    # --- Database ---
    # SQLite by default; swap for e.g. postgresql+psycopg://user:pass@host/db
    database_url: str = "sqlite:///./data/unbound.db"

    # --- Security ---
    # Master key for encrypting secrets at rest. If unset, a key is generated on
    # first run and persisted to ``data/secret.key`` (chmod 600). Provide your own
    # in production so it survives volume resets and can be rotated deliberately.
    secret_key: str | None = None
    # Signing key for session/JWT cookies. Derived from secret_key if unset.
    session_secret: str | None = None
    session_ttl_seconds: int = 60 * 60 * 24 * 7  # 7 days
    cookie_secure: bool = True
    cookie_name: str = "unbound_session"
    # Comma-separated allowed origins for CORS in dev (SPA + API share origin in prod).
    cors_origins: list[str] = Field(default_factory=list)

    # --- Worker / pipeline ---
    max_concurrent_downloads: int = 2
    ffmpeg_path: str = "ffmpeg"
    # Broker: "db" (default, zero-dependency) or "redis"
    queue_backend: str = "db"
    redis_url: str = "redis://localhost:6379/0"

    # --- Update checks ---
    update_check_enabled: bool = True
    github_repo: str = "hankscafe/unbound"
    # Only needed while the repo is private: a read-only token so the release
    # check can see releases (UNBOUND_GITHUB_TOKEN). Public repos need none.
    github_token: str | None = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.downloads_dir, self.library_dir, self.log_dir):
            p.mkdir(parents=True, exist_ok=True)

    def resolve_secret_key(self) -> str:
        """Return the master secret key, generating & persisting one if absent."""
        if self.secret_key:
            return self.secret_key
        self.data_dir.mkdir(parents=True, exist_ok=True)
        key_file = self.data_dir / "secret.key"
        if key_file.exists():
            return key_file.read_text(encoding="utf-8").strip()
        generated = secrets.token_urlsafe(48)
        key_file.write_text(generated, encoding="utf-8")
        try:
            key_file.chmod(0o600)
        except (OSError, NotImplementedError):
            # chmod is a no-op on some Windows filesystems; acceptable.
            pass
        return generated

    def resolve_session_secret(self) -> str:
        return self.session_secret or self.resolve_secret_key()


@lru_cache
def get_settings() -> Settings:
    return Settings()
