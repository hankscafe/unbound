"""Pydantic request/response schemas for the REST API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# --- Auth / setup ----------------------------------------------------------


class SetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    email: str | None = None
    consent: bool = Field(description="User acknowledges personal-use-only terms")


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str
    email: str | None
    role: str
    totp_enabled: bool = False


class LoginResult(BaseModel):
    """Login outcome: either signed in, or a 2FA code is required."""

    mfa_required: bool = False
    mfa_token: str | None = None  # short-lived, exchanged with the TOTP code
    user: UserOut | None = None


class Login2FARequest(BaseModel):
    mfa_token: str
    code: str  # TOTP code or a recovery code


class TwoFASetupOut(BaseModel):
    secret: str  # base32, for manual entry
    otpauth_uri: str
    qr: str  # data: URI PNG


class TwoFACodeRequest(BaseModel):
    code: str


class TwoFAEnableOut(BaseModel):
    recovery_codes: list[str]  # shown once


class StatusOut(BaseModel):
    setup_required: bool
    authenticated: bool
    consent_acknowledged: bool
    secret_key_rotated: bool
    version: str


# --- Audible accounts ------------------------------------------------------


class AccountCreate(BaseModel):
    label: str
    marketplace: str = "us"
    badge_color: str = "#F8991C"


class AccountOut(BaseModel):
    id: int
    label: str
    marketplace: str
    account_owner_email: str | None
    status: str
    badge_color: str
    last_sync_at: datetime | None
    last_error: str | None


class GuidedLinkStart(BaseModel):
    email: str
    password: str


class LinkOtp(BaseModel):
    otp: str


class LinkCaptcha(BaseModel):
    captcha_answer: str


class ExternalLinkStartOut(BaseModel):
    login_url: str
    flow_id: str


class ExternalLinkComplete(BaseModel):
    flow_id: str
    response_url: str


class LinkStepOut(BaseModel):
    """Result of a linking step: either done, or a prompt for the next input."""

    status: str  # linked | needs_otp | needs_captcha | error
    flow_id: str | None = None
    captcha_image_url: str | None = None
    message: str | None = None


# --- Library / books -------------------------------------------------------


class BookOut(BaseModel):
    id: int
    audible_account_id: int
    account_label: str | None = None
    account_badge_color: str | None = None
    asin: str
    title: str
    subtitle: str | None
    authors: str | None
    narrators: str | None
    series: str | None
    series_sequence: str | None
    runtime_minutes: int | None
    cover_url: str | None
    is_aax_available: bool
    excluded: bool
    purchase_date: datetime | None = None
    audible_url: str | None = None
    abs_url: str | None = None  # "open in AudiobookShelf" link (completed downloads only)
    output_path: str | None = None
    job_state: str | None = None
    job_progress: float | None = None


class ExcludeRequest(BaseModel):
    excluded: bool


class BatchExclude(BaseModel):
    book_ids: list[int]
    excluded: bool


class BatchDownload(BaseModel):
    book_ids: list[int]


# --- Jobs ------------------------------------------------------------------


class JobOut(BaseModel):
    id: int
    book_id: int
    book_title: str | None = None
    state: str
    progress: float
    format: str | None
    error_message: str | None
    attempt_count: int
    updated_at: datetime


# --- Library profiles ------------------------------------------------------


class LibraryProfileIn(BaseModel):
    name: str
    root_path: str
    folder_template: str = "{author}/{series}"
    filename_template: str = "{title}"
    audio_format: str = "m4b"
    embed_cover: bool = True
    embed_chapters: bool = True
    is_default: bool = False
    audible_account_id: int | None = None


class LibraryProfileOut(LibraryProfileIn):
    id: int


# --- Stats (Homepage widget) ----------------------------------------------


class StatsOut(BaseModel):
    accounts_linked: int
    accounts_total: int
    books_total: int
    downloaded: int
    in_progress: int
    failed: int
    excluded: int
    pending: int
    update_available: bool
    current_version: str
    latest_version: str | None
    # Storage / library health
    downloaded_bytes: int = 0
    library_path: str | None = None
    library_ok: bool = True
    library_total_bytes: int | None = None
    library_free_bytes: int | None = None
    library_warning: str | None = None


# --- API keys --------------------------------------------------------------


class ApiKeyCreate(BaseModel):
    name: str


class ApiKeyOut(BaseModel):
    id: int
    name: str
    scope: str
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None


class ApiKeyCreatedOut(ApiKeyOut):
    key: str  # shown once, on creation


# --- Integrations (scheduling + AudiobookShelf) ---------------------------


class IntegrationsSettings(BaseModel):
    schedule_enabled: bool = False
    schedule_interval_hours: int = Field(default=12, ge=1, le=168)
    auto_download_new: bool = False
    abs_url: str | None = None
    abs_library_id: str | None = None
    # ABS API token — write-only: send to set/replace; never echoed back.
    abs_token: str | None = None
    abs_token_set: bool = False  # read-only indicator that a token is stored
    # Notifications (Apprise URLs, newline/comma separated). Stored encrypted.
    notify_urls: str | None = None
    notify_on_new_books: bool = False
    notify_on_complete: bool = False
    notify_on_failure: bool = True
