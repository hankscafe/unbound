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
            resp = httpx.get(url, timeout=10, headers={"Accept": "application/vnd.github+json"})
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
