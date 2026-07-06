"""Stream the encrypted audiobook to local storage with progress callbacks."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx

from app.core.logging import get_logger

log = get_logger("pipeline.download")

ProgressFn = Callable[[int, int | None], None]

# Audible's content CDN (CloudFront) rejects requests without the Audible app
# User-Agent with 403 Forbidden — even though the offline URL is already signed.
AUDIBLE_USER_AGENT = "Audible/671 CFNetwork/1240.0.4 Darwin/20.6.0"


def _content_range_total(resp: httpx.Response) -> int | None:
    """Total size from a 206 'Content-Range: bytes X-Y/Z' header."""
    cr = resp.headers.get("content-range", "")
    if "/" in cr:
        tail = cr.rsplit("/", 1)[-1].strip()
        if tail.isdigit():
            return int(tail)
    return None


def download_file(
    url: str,
    dest: Path,
    *,
    on_progress: ProgressFn | None = None,
    headers: dict[str, str] | None = None,
    resume: bool = True,
) -> Path:
    """Stream a download to ``dest``, resuming a prior partial ``.part`` via HTTP Range.

    If the server ignores the Range request (returns 200), the partial file is
    discarded and the download restarts from the beginning.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req_headers = {"User-Agent": AUDIBLE_USER_AGENT, **(headers or {})}

    existing = tmp.stat().st_size if (resume and tmp.exists()) else 0
    if existing:
        req_headers["Range"] = f"bytes={existing}-"

    with httpx.stream("GET", url, headers=req_headers, timeout=None, follow_redirects=True) as resp:
        # Range unsatisfiable (e.g. .part already complete/stale) → restart clean.
        if existing and resp.status_code == 416:
            resp.close()
            tmp.unlink(missing_ok=True)
            return download_file(url, dest, on_progress=on_progress, headers=headers, resume=False)
        resp.raise_for_status()

        resuming = existing and resp.status_code == 206
        if existing and not resuming:  # server ignored Range → start over
            existing = 0
        mode = "ab" if resuming else "wb"

        if resuming:
            total = _content_range_total(resp)
        else:
            cl = int(resp.headers.get("content-length", 0))
            total = cl or None

        done = existing
        if on_progress and resuming:
            log.info("download_resumed", offset=existing)
        with tmp.open(mode) as fh:
            for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                fh.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)

    tmp.replace(dest)
    log.info("download_complete", bytes=done)
    return dest
