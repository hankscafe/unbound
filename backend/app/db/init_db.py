"""First-run initialization: seed settings and detect whether setup is needed.

Unbound has no baked-in credentials. On first run the UI drives a setup flow that
creates the single admin user (:func:`create_admin`). Until an admin exists the app
reports ``setup_required`` and only the setup + health endpoints are usable.
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.core.runtime import get_secret_box
from app.core.security import hash_password
from app.db.models import AppSetting, User, UserRole

# Setting keys
SETTING_CONSENT = "consent_acknowledged"
SETTING_KEY_FINGERPRINT = "secret_key_fingerprint"
SETTING_THEME = "theme"
# Scheduling
SETTING_SCHEDULE_ENABLED = "schedule_enabled"
SETTING_SCHEDULE_INTERVAL_HOURS = "schedule_interval_hours"
SETTING_AUTO_DOWNLOAD = "auto_download_new"
# AudiobookShelf integration
SETTING_ABS_URL = "abs_url"
SETTING_ABS_LIBRARY_ID = "abs_library_id"
SETTING_ABS_TOKEN = "abs_token_enc"  # encrypted at rest
# Auto-exclude books that already exist in AudiobookShelf (weren't downloaded by us)
SETTING_ABS_AUTO_EXCLUDE = "abs_auto_exclude"
# Notifications (Apprise). notify_urls is stored encrypted at rest.
SETTING_NOTIFY_URLS = "notify_urls_enc"
SETTING_NOTIFY_NEW_BOOKS = "notify_on_new_books"
SETTING_NOTIFY_COMPLETE = "notify_on_complete"
SETTING_NOTIFY_FAILURE = "notify_on_failure"
SETTING_NOTIFY_UPDATE = "notify_on_update"
# Update announcements are sent once per version; this remembers the last one.
SETTING_LAST_NOTIFIED_VERSION = "last_notified_update_version"


def get_bool(session: Session, key: str, default: bool = False) -> bool:
    val = get_setting(session, key)
    return default if val is None else val.lower() in ("1", "true", "yes", "on")


def admin_exists(session: Session) -> bool:
    return session.exec(select(User).where(User.role == UserRole.admin)).first() is not None


def setup_required(session: Session) -> bool:
    return not admin_exists(session)


def get_setting(session: Session, key: str, default: str | None = None) -> str | None:
    row = session.get(AppSetting, key)
    return row.value if row and row.value is not None else default


def set_setting(session: Session, key: str, value: str | None) -> None:
    row = session.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value=value)
        session.add(row)
    else:
        row.value = value
    session.commit()


def create_admin(session: Session, *, username: str, password: str, email: str | None) -> User:
    if admin_exists(session):
        raise ValueError("An admin user already exists")
    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        role=UserRole.admin,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def seed_defaults(session: Session) -> None:
    """Idempotently record the current secret-key fingerprint and defaults."""
    fp = get_secret_box().fingerprint
    if get_setting(session, SETTING_KEY_FINGERPRINT) is None:
        set_setting(session, SETTING_KEY_FINGERPRINT, fp)
    if get_setting(session, SETTING_THEME) is None:
        set_setting(session, SETTING_THEME, "audible-dark")


def secret_key_rotated(session: Session) -> bool:
    """True if the master key changed since last boot (existing ciphertext at risk)."""
    stored = get_setting(session, SETTING_KEY_FINGERPRINT)
    return stored is not None and stored != get_secret_box().fingerprint
