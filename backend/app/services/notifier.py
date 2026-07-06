"""Notifications via Apprise (ntfy, Discord, Telegram, email, webhooks, ...).

Target URLs are stored encrypted at rest (they often embed tokens) and decrypted
only in-memory when sending. Sending is best-effort and never breaks a pipeline.
"""

from __future__ import annotations

import re

from sqlmodel import Session

from app.core.logging import get_logger
from app.core.runtime import get_secret_box
from app.db import init_db
from app.db.session import engine

log = get_logger("services.notifier")


def get_urls(session: Session) -> list[str]:
    enc = init_db.get_setting(session, init_db.SETTING_NOTIFY_URLS)
    if not enc:
        return []
    try:
        raw = get_secret_box().decrypt_str(enc)
    except Exception:
        log.warning("notify_urls_decrypt_failed")
        return []
    return [u.strip() for u in re.split(r"[\n,]", raw) if u.strip()]


def set_urls(session: Session, urls_text: str | None) -> None:
    """Encrypt and store the newline/comma-separated Apprise URLs (or clear)."""
    text = (urls_text or "").strip()
    value = get_secret_box().encrypt(text) if text else None
    init_db.set_setting(session, init_db.SETTING_NOTIFY_URLS, value)


def _event_enabled(session: Session, key: str, default: bool = False) -> bool:
    return init_db.get_bool(session, key, default)


def send(title: str, body: str) -> None:
    """Fire a notification to all configured targets (best-effort)."""
    with Session(engine) as session:
        urls = get_urls(session)
    if not urls:
        return
    try:
        import apprise  # imported lazily so the app runs without the dep in dev

        ap = apprise.Apprise()
        for u in urls:
            ap.add(u)
        ok = ap.notify(title=title, body=body)
        log.info("notification_sent", ok=bool(ok), targets=len(urls))
    except Exception as exc:  # pragma: no cover - network/dep
        log.warning("notification_failed", error=str(exc))


def send_if_enabled(event_key: str, title: str, body: str, default: bool = False) -> None:
    with Session(engine) as session:
        if not _event_enabled(session, event_key, default):
            return
    send(title, body)
