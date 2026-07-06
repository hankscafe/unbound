"""Lightweight update check against GitHub releases (cached in-process)."""

from __future__ import annotations

import threading
import time

import httpx

from app import __version__
from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("services.updates")
settings = get_settings()

_CACHE_TTL = 60 * 60 * 6  # 6 hours
_lock = threading.Lock()
_cache: dict[str, object] = {"ts": 0.0, "latest": None}


def _normalize(v: str) -> tuple[int, ...]:
    v = v.lstrip("vV")
    parts = []
    for p in v.split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts)


def check_for_update(force: bool = False) -> dict:
    if not settings.update_check_enabled:
        return {"current_version": __version__, "latest_version": None, "update_available": False}

    now = time.time()
    with _lock:
        fresh = (now - float(_cache["ts"])) < _CACHE_TTL  # type: ignore[arg-type]
        cached_latest = _cache["latest"]
    if fresh and not force and cached_latest is not None:
        latest = cached_latest  # type: ignore[assignment]
    else:
        latest = None
        try:
            url = f"https://api.github.com/repos/{settings.github_repo}/releases/latest"
            headers = {"Accept": "application/vnd.github+json"}
            if settings.github_token:
                headers["Authorization"] = f"Bearer {settings.github_token}"
            resp = httpx.get(url, timeout=10, headers=headers)
            if resp.status_code == 200:
                latest = resp.json().get("tag_name")
        except Exception as exc:  # pragma: no cover - network
            log.info("update_check_failed", error=str(exc))
        with _lock:
            _cache["ts"] = now
            _cache["latest"] = latest

    available = bool(latest) and _normalize(str(latest)) > _normalize(__version__)
    return {
        "current_version": __version__,
        "latest_version": latest,
        "update_available": available,
    }


def repo_url() -> str:
    return f"https://github.com/{settings.github_repo}"


def check_and_notify() -> dict:
    """Run the update check and alert admins ONCE per new version.

    Called periodically by the scheduler. On the first sighting of a new latest
    version this logs an event, pushes an SSE update (in-app banner refresh), and
    sends an Apprise notification if that event is enabled.
    """
    from sqlmodel import Session

    from app.db import init_db
    from app.db.models import EventLog
    from app.db.session import engine
    from app.services import events, notifier

    result = check_for_update()
    if not result["update_available"]:
        return result
    latest = str(result["latest_version"])
    with Session(engine) as session:
        if init_db.get_setting(session, init_db.SETTING_LAST_NOTIFIED_VERSION) == latest:
            return result  # already announced this one
        init_db.set_setting(session, init_db.SETTING_LAST_NOTIFIED_VERSION, latest)
        session.add(EventLog(category="system",
                             message=f"Update available: {latest} (current {__version__})"))
        session.commit()
    events.publish({"type": "update_available", "latest_version": latest})
    notifier.send_if_enabled(
        init_db.SETTING_NOTIFY_UPDATE,
        f"Unbound: update available ({latest})",
        f"You are on {__version__}. Release notes: {repo_url()}/releases/latest",
        default=True,
    )
    log.info("update_available", latest=latest, current=__version__)
    return result
