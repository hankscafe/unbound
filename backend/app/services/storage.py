"""Storage stats + library-path health for the dashboard.

Catches a common self-hosting mistake: a library root that isn't a valid absolute
path *inside the app's runtime* (e.g. a Windows UNC path like ``\\\\host\\share`` set on
a Linux container). Such a path is relative there, so files silently land in the
container's ephemeral filesystem and vanish on restart/rebuild.
"""

from __future__ import annotations

import os
import shutil
from pathlib import PurePosixPath, PureWindowsPath
from pathlib import Path

from sqlmodel import Session, func, select

from app.core.config import get_settings
from app.db.models import DownloadJob, JobState, LibraryProfile

settings = get_settings()


def _looks_like_unc_or_windows(raw: str) -> bool:
    # Backslashes / drive letters are meaningful on Windows but not on POSIX.
    return "\\" in raw or PureWindowsPath(raw).drive != ""


def path_problem(raw: str) -> str | None:
    """Return a human-readable reason a library root is unsafe, or None if it's fine.

    Shared by the dashboard warning and library-profile save-time validation.
    """
    if not raw or not raw.strip():
        return "Library path is empty."
    if _looks_like_unc_or_windows(raw) and os.name != "nt":
        return (
            f"Library path '{raw}' looks like a Windows/UNC path but the app runs on "
            "Linux, where it is treated as a *relative* path — downloads will be written "
            "to a non-persistent location and lost on restart. Mount your target into the "
            "container and set the root to a path like '/data/library'."
        )
    if not PurePosixPath(raw).is_absolute() and not PureWindowsPath(raw).is_absolute():
        return (
            f"Library path '{raw}' is not absolute; downloads may go to a non-persistent "
            "location. Use an absolute, mounted path such as '/data/library'."
        )
    return None


def _nearest_existing(path: Path) -> Path | None:
    p = path
    while True:
        if p.exists():
            return p
        if p.parent == p:
            return None
        p = p.parent


def _disk_usage(path: Path) -> tuple[int | None, int | None]:
    target = _nearest_existing(path)
    if target is None:
        return None, None
    try:
        usage = shutil.disk_usage(str(target))
        return usage.total, usage.free
    except OSError:
        return None, None


def compute_storage(session: Session) -> dict:
    # Total bytes fetched (sum of completed download sizes we recorded).
    downloaded_bytes = (
        session.exec(
            select(func.coalesce(func.sum(DownloadJob.bytes_total), 0)).where(
                DownloadJob.state == JobState.completed
            )
        ).one()
        or 0
    )

    profile = session.exec(
        select(LibraryProfile).where(LibraryProfile.is_default == True)  # noqa: E712
    ).first() or session.exec(select(LibraryProfile)).first()

    if profile is None:
        return {
            "downloaded_bytes": int(downloaded_bytes),
            "library_path": None,
            "library_ok": False,
            "library_total_bytes": None,
            "library_free_bytes": None,
            "library_warning": "No library profile configured.",
        }

    raw = profile.root_path
    warning = path_problem(raw)
    ok = warning is None

    total, free = (None, None)
    if ok:
        total, free = _disk_usage(Path(raw))
        root = Path(raw)
        if root.exists() and not os.access(root, os.W_OK):
            ok = False
            warning = f"Library path '{raw}' is not writable by the app."

    return {
        "downloaded_bytes": int(downloaded_bytes),
        "library_path": raw,
        "library_ok": ok,
        "library_total_bytes": total,
        "library_free_bytes": free,
        "library_warning": warning,
    }
