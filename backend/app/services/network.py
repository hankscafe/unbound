"""Network availability tracking for the download pipeline.

When downloads hit transport-level failures the network is marked down: queued
work parks instead of burning retry attempts, and the scheduler skips sync /
auto-download cycles. A lightweight probe re-checks connectivity (at most once
per PROBE_INTERVAL) and, on recovery, parked jobs are resumed automatically.

"Up" means we can complete a TCP/TLS exchange with a well-known host — any HTTP
status (even 403/404) counts; only transport errors (DNS, connect, TLS,
timeout) count as down.
"""

from __future__ import annotations

import threading
import time

import httpx

from app.core.logging import get_logger

log = get_logger("services.network")

PROBE_INTERVAL = 60.0  # seconds between probes while down
_PROBE_URLS = (
    "https://api.audible.com/1.0/oauth2/token",  # the host that actually matters
    "https://www.google.com/generate_204",  # generic fallback
)

_lock = threading.Lock()
_online = True
# Start "already due": time.monotonic() is seconds-since-boot on Linux, so a
# freshly booted host would otherwise suppress the first recovery probe.
_last_probe = -PROBE_INTERVAL


def probe() -> bool:
    """One connectivity check: any HTTP response from any probe host counts as up."""
    for url in _PROBE_URLS:
        try:
            httpx.head(url, timeout=5.0, follow_redirects=False)
            return True
        except Exception:
            continue
    return False


def _set_online(value: bool, reason: str = "") -> None:
    """Flip the flag; log + record the transition once (not every probe)."""
    global _online
    with _lock:
        if _online == value:
            return
        _online = value
    from sqlmodel import Session

    from app.db.models import EventLog
    from app.db.session import engine
    from app.services import events

    message = (
        "Network connection restored — resuming downloads"
        if value
        else f"Network appears to be down — pausing downloads ({reason or 'transport error'})"
    )
    log.warning("network_state", online=value, reason=reason)
    try:
        with Session(engine) as session:
            session.add(EventLog(level="info" if value else "warning",
                                 category="system", message=message))
            session.commit()
        events.publish({"type": "network", "online": value})
    except Exception:  # never let bookkeeping break the pipeline
        pass


def is_online() -> bool:
    """Current belief about connectivity (no probe)."""
    with _lock:
        return _online


def mark_down(reason: str = "") -> None:
    """Report a transport-level failure (called from the download pipeline)."""
    global _last_probe
    with _lock:
        _last_probe = time.monotonic()
    _set_online(False, reason)


def ensure_online() -> bool:
    """True if we're online; while down, re-probe at most every PROBE_INTERVAL."""
    global _last_probe
    with _lock:
        if _online:
            return True
        due = time.monotonic() - _last_probe >= PROBE_INTERVAL
        if due:
            _last_probe = time.monotonic()
    if not due:
        return False
    if probe():
        _set_online(True)
        return True
    return False


def recheck_and_resume() -> bool:
    """Scheduler hook: probe while down; True exactly when connectivity returns
    (the caller then re-enqueues parked jobs)."""
    if is_online():
        return False
    return ensure_online()
