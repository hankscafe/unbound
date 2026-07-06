"""SQLModel database models for Unbound.

The schema is single-admin today but intentionally multi-user-ready: ``User`` carries
a ``role`` and Audible accounts / library profiles are independent of any one user.
No raw Audible password is ever persisted — only the encrypted device auth blob.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import Column
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Text
from sqlmodel import JSON, Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- Enums -----------------------------------------------------------------


class UserRole(str, enum.Enum):
    admin = "admin"
    viewer = "viewer"


class AccountStatus(str, enum.Enum):
    unlinked = "unlinked"
    linking = "linking"       # awaiting OTP / captcha / external callback
    linked = "linked"
    needs_reauth = "needs_reauth"
    error = "error"


class JobState(str, enum.Enum):
    pending = "pending"
    queued = "queued"
    downloading = "downloading"
    downloaded = "downloaded"
    decrypting = "decrypting"
    tagging = "tagging"
    moving = "moving"
    completed = "completed"
    failed = "failed"
    excluded = "excluded"
    cancelled = "cancelled"


class AudioFormat(str, enum.Enum):
    m4b = "m4b"
    mp3 = "mp3"
    opus = "opus"


# --- Tables ----------------------------------------------------------------


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    email: str | None = None
    password_hash: str
    role: UserRole = Field(
        default=UserRole.admin, sa_column=Column(SAEnum(UserRole), nullable=False)
    )
    is_active: bool = True
    created_at: datetime = Field(default_factory=_utcnow)
    last_login_at: datetime | None = None


class AudibleAccount(SQLModel, table=True):
    __tablename__ = "audible_accounts"

    id: int | None = Field(default=None, primary_key=True)
    label: str  # friendly name shown in UI / used as badge text
    marketplace: str = "us"  # locale code: us, uk, de, fr, ca, au, jp, ...
    # Owner display: Audible account email if available, else the holder's name
    # (Audible's customer_info often omits email). Display only.
    account_owner_email: str | None = None
    status: AccountStatus = Field(
        default=AccountStatus.unlinked, sa_column=Column(SAEnum(AccountStatus), nullable=False)
    )
    badge_color: str = "#F8991C"  # per-account badge accent
    # AES-GCM-encrypted device auth blob (tokens/keys). NULL until linked.
    encrypted_auth_blob: str | None = Field(default=None, sa_column=Column(Text))
    # Encrypted cached activation bytes (AAX). NULL until fetched.
    encrypted_activation_bytes: str | None = Field(default=None, sa_column=Column(Text))
    last_error: str | None = None
    last_sync_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class Book(SQLModel, table=True):
    __tablename__ = "books"

    id: int | None = Field(default=None, primary_key=True)
    audible_account_id: int = Field(foreign_key="audible_accounts.id", index=True)
    asin: str = Field(index=True)
    title: str
    subtitle: str | None = None
    authors: str | None = None
    narrators: str | None = None
    series: str | None = None
    series_sequence: str | None = None
    runtime_minutes: int | None = None
    cover_url: str | None = None
    purchase_date: datetime | None = None
    is_aax_available: bool = False  # AAX (activation-bytes) fallback offered by Audible
    excluded: bool = Field(default=False, index=True)  # admin toggle: never download
    abs_item_id: str | None = None  # resolved AudiobookShelf library item id (deep-link)
    raw_metadata: dict | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class DownloadJob(SQLModel, table=True):
    __tablename__ = "download_jobs"

    id: int | None = Field(default=None, primary_key=True)
    book_id: int = Field(foreign_key="books.id", index=True)
    state: JobState = Field(
        default=JobState.pending, sa_column=Column(SAEnum(JobState), nullable=False, index=True)
    )
    progress: float = 0.0  # 0..100
    format: str | None = None  # aax | aaxc
    source_path: str | None = None   # encrypted download location
    output_path: str | None = None   # final library path
    bytes_total: int | None = None
    bytes_done: int | None = None
    error_message: str | None = Field(default=None, sa_column=Column(Text))
    attempt_count: int = 0
    library_profile_id: int | None = Field(default=None, foreign_key="library_profiles.id")
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class LibraryProfile(SQLModel, table=True):
    __tablename__ = "library_profiles"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    is_default: bool = False
    root_path: str
    # Templates use {author}, {title}, {series}, {series_seq}, {narrator}, {asin}, {year}
    folder_template: str = "{author}/{series}"
    filename_template: str = "{title}"
    audio_format: AudioFormat = Field(
        default=AudioFormat.m4b, sa_column=Column(SAEnum(AudioFormat), nullable=False)
    )
    embed_cover: bool = True
    embed_chapters: bool = True
    # Optional: restrict this profile to a single Audible account.
    audible_account_id: int | None = Field(default=None, foreign_key="audible_accounts.id")
    created_at: datetime = Field(default_factory=_utcnow)


class AppSetting(SQLModel, table=True):
    __tablename__ = "app_settings"

    key: str = Field(primary_key=True)
    value: str | None = Field(default=None, sa_column=Column(Text))
    updated_at: datetime = Field(default_factory=_utcnow)


class ApiKey(SQLModel, table=True):
    __tablename__ = "api_keys"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    key_hash: str = Field(index=True)  # sha256 of the key; raw shown once on creation
    scope: str = "read"  # read-only for widget consumers
    is_active: bool = True
    created_at: datetime = Field(default_factory=_utcnow)
    last_used_at: datetime | None = None


class EventLog(SQLModel, table=True):
    __tablename__ = "event_log"

    id: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=_utcnow, index=True)
    level: str = "info"
    category: str = "system"  # system | account | library | job | auth
    message: str
    job_id: int | None = None
    account_id: int | None = None
    book_id: int | None = None
    data: dict | None = Field(default=None, sa_column=Column(JSON))
